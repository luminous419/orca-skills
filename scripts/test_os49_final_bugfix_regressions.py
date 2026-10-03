#!/usr/bin/env python3
"""OS-49 FINAL BUGFIX round: one direct regression per PR #37 review finding 5954727286.

Every test here is written so that it FAILS against HEAD e5dead8 and passes after. Each
class names the finding it locks and states the pre-fix failure mode, because a regression
that passes before and after proves nothing. The revert experiment that confirms exactly
that is reported in the Worker Result.

  B1  stale verified evidence after a rejected model switch   -> StaleSessionEvidenceTests
                                                                 PreSelectionRejectionTests
                                                                 SessionEvidenceRecoveryTests
  N1  rollback leaves terminal-row model state behind         -> TotalRollbackTests
  N2  FakeAdapter.capabilities() imports agent_profile with   -> NoDriverImportTests
      no model driver

Iteration 2 adds the two blocking findings of the mandatory FINAL ADVERSARIAL REVIEW,
which failed iteration 1 after the phase Reviewer had passed it. Both are B1 routes the
first round left open:

  R1  a driver raising a direct BaseException (KeyboardInterrupt /  -> InterruptedSelectionTests
      SystemExit) after selection began bypassed invalidation
  R2  the missing same-command counterpart was still decided        -> PairAdmissionPreSelectionTests
      AFTER the driver had been invoked

The B1 story needs BOTH of the first two classes, and they are not alternatives.

The review's required direction has two halves. The first is "reject deterministic
role/session conflicts BEFORE invoking model selection whenever possible". Applying it to
the M5 session check -- the one check in `_verify_model_identity()` that reads nothing but
harness state -- means that on the literal reproduction route (reviewer verified on R, then
worker attempted on R) the driver is NEVER CALLED a second time. No selection is requested,
so the session is never switched, so R's reviewer evidence never stops describing R, so
there is no stale evidence on that route for a later delivery to lean on. The literal
step-2 refusal reason of the reproduction (`WORKER_REVIEWER_MUST_DIFFER`) is therefore
unreachable from a POST-selection state after the fix, and `PreSelectionRejectionTests`
locks that by asserting the driver call count does not increase -- positive proof that the
mutation window is gone, rather than a refusal that only proves it was survived.

The second half is "once model selection HAS executed, a failed post-selection validation
must not restore or retain previously verified evidence as authoritative". That half is
what `StaleSessionEvidenceTests` locks, on the route where the switch really does happen:
re-verifying one session whose resolved model drifted refuses at leg (k), AFTER the driver
ran. Pre-fix the old record for that session survived untouched and granted pair admission
to a later delivery on a different session -- which is B1 itself, with the identical
consequence the review describes and reached through a path the hoist cannot close.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from scripts.agent_profile import REASON_WORKER_REVIEWER_MUST_DIFFER
from scripts.deterministic_workflow.fake_adapter import InProcessModelDriver
from scripts.orca_runtime_harness import (
    MODEL_SELECTION_AMBIGUOUS,
    MODEL_SELECTION_PAIR_UNADMITTED,
    MODEL_SELECTION_UNVERIFIED,
    OrcaRuntimeError,
    OrcaRuntimeHarness,
)
from scripts.test_orca_runtime_contract import SequentialTerminalExec
from scripts.test_os49_bugfix_regressions import (
    _verified_resolver,
    resolving_per_session,
)
from scripts.test_os49_delivery_barrier import (
    BarrierTestCase,
    DISTINCT_COMMAND_PROFILE,
    SPLIT_PROFILE,
    routing_from,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The neutral shape every model cell carries on a row with NO evidence, i.e. exactly what
#: `register_terminal()` creates. Spelled out here rather than imported so the test states
#: the expected shape independently of the constant the production code reads.
def sessions_resolving(*sequences: list[str]):
    """One model SEQUENCE per TERMINAL, assigned in first-seen order.

    `sequences[n]` is the list of resolved models terminal `n` reports, in the order that
    terminal is asked, with the last entry repeating for every further call on it.

    Neither shipped helper can express these scenarios. `resolving_per_session` pins one
    model per terminal forever, so no session can ever drift -- and a drifting session is
    the premise of the POST-selection route. `drifting` indexes by GLOBAL call order, which
    breaks the moment a test calls the delivery barrier: the barrier selects again, so a
    delivery silently consumes the next entry and the session it was asked about appears to
    have changed model. Keying on the terminal and counting per terminal is what lets a
    test say "R drifts, W does not" and have a delivery on W mean what it says.
    """
    assigned: dict[str, list[str]] = {}
    calls: dict[str, int] = {}

    def pick(ticket):
        handle = ticket.terminal
        if handle not in assigned:
            assigned[handle] = list(
                sequences[min(len(assigned), len(sequences) - 1)]
            )
        sequence = assigned[handle]
        index = calls.get(handle, 0)
        calls[handle] = index + 1
        return sequence[min(index, len(sequence) - 1)]

    return _verified_resolver(pick)


NO_MODEL_CELLS = {
    "resolved_model": "",
    "model_state": "none",
    "model_request_method": "",
    "model_request_evidence": "",
    "model_observed_at_dispatch": "",
}


class ModelSessionTestCase(BarrierTestCase):
    """A same-command model-aware pair over a recorder that hands out fresh handles.

    SPLIT_PROFILE's Worker and Reviewer both run `claude`, so the pair is SAME-COMMAND and
    pair admission is live -- which is the whole point: a distinct-command pair is
    independent on its commands alone and would never read counterpart evidence at all.
    """

    def harness_for(self, driver):
        recorder = SequentialTerminalExec()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"), model_driver=driver
        )
        return recorder, harness

    def session(self, harness, role):
        return harness.create_fake_terminal(
            role, "pass" if role == "reviewer" else "complete",
            iteration=1, phase="implementation",
        )

    def switches(self, driver) -> int:
        """How many times the driver was asked to SELECT -- i.e. physical switches.

        `RecordingDriver` appends one ("request", ordinal) and one ("observe", ordinal)
        entry per `select_and_verify()` call, so the call count is half the entries. This
        is the number the B1 hoist is measured by.
        """
        return len(driver.calls) // 2

    def deliver(self, harness, terminal, role, *, attempt=1, task_id="task_delivery"):
        """The DELIVERY barrier -- `require_pair_admission=True` -- and nothing else.

        `start_worker()` reaches the same barrier as its first statement, and the rung-3/4
        delivery tests already cover that wiring. Calling the barrier directly keeps these
        tests about ADMISSION rather than about `worker-start` receipt shapes, and a
        refusal here is a refusal there.
        """
        harness._verify_model_identity(
            task_id=task_id, terminal=terminal, role=role, phase="implementation",
            attempt=attempt, require_pair_admission=True,
        )

    def assertNoRecordFor(self, harness, terminal, *, role="reviewer") -> None:
        """Nothing anywhere in the harness still describes `terminal` as verified.

        Four separate places record it, and the pre-fix defect left all four standing, so
        all four are asserted. A test that checked only `_model_identity` would pass
        against a fix that cleared the map and left the row advertising `verified`.
        """
        record = harness._model_identity.get(("implementation", role))
        self.assertIsNone(
            record,
            f"an authoritative {role} record survived: "
            f"{record and (record.resolved_model, record.observed_at_terminal)}",
        )
        self.assertNotIn(terminal, harness._model_session_identity)
        self.assertNotIn(terminal, harness._model_pending_evidence)
        row = harness._terminals.get(terminal)
        if row is not None:
            self.assertEqual(
                {cell: row.get(cell) for cell in NO_MODEL_CELLS}, NO_MODEL_CELLS,
                "the terminal row still advertises verified model state",
            )


# ---- B1, half 2: a POST-selection refusal stales the session ---------------------------

class StaleSessionEvidenceTests(ModelSessionTestCase):
    """B1. Evidence for a session a refused selection may have switched is not authoritative.

    Pre-fix failure mode: `_verify_model_identity()` recorded only on acceptance and did
    nothing at all on refusal -- which reads as safe and is not. `select_and_verify()` is
    the act that SWITCHES the session, and it runs ~220 lines before the legs that can
    refuse. So a refusal after it left the session physically on the new model while every
    previously accepted record naming that session still said the old one, and
    `effective_identity_independent()` -- comparing two RESOLVED models that were now both
    the same physical model -- returned independent=True and admitted a later delivery.
    """

    def test_the_whole_four_step_delivery_scenario_fails_closed(self) -> None:
        """The complete reproduction, end to end, as ONE test -- not just the refusal.

        The immediate refusal at step 2 was never the bug; the bug was step 4. So step 4
        is what this asserts, by refusal REASON and not merely by "something raised".
        """
        # R resolves model-b, then model-a: its resolved model DRIFTS on re-verification,
        # which is what makes step 2 a POST-selection refusal. Load-bearing that it
        # drifts -- an unchanged resolved model is accepted and there would be no refusal
        # to test. W resolves model-a and stays there, so step 4's delivery is refused for
        # the pair reason under test rather than for a drift of its own.
        driver = sessions_resolving(["model-b", "model-a"], ["model-a"])
        recorder, harness = self.harness_for(driver)

        # 1. the Reviewer is positively verified on terminal R as model-b.
        reviewer = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_rev", reviewer, role="reviewer", phase="implementation", attempt=1
        )
        self.assertEqual(
            harness._model_identity[("implementation", "reviewer")].resolved_model,
            "model-b",
        )

        # 2. the SAME terminal R is re-verified and the selection resolves differently.
        #    Refused -- but only AFTER the driver has already switched the session.
        before = self.switches(driver)
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.verify_model_identity(
                "task_rev2", reviewer, role="reviewer", phase="implementation", attempt=2
            )
        self.assertTrue(
            str(caught.exception).startswith(MODEL_SELECTION_AMBIGUOUS),
            f"expected {MODEL_SELECTION_AMBIGUOUS}, got: {caught.exception}",
        )
        self.assertEqual(
            self.switches(driver), before + 1,
            "this route's premise is that the physical switch DID happen before the "
            "refusal; if the driver was not called the test is no longer about B1",
        )
        # The state assertion the review asks for by name: after step 2 there is no
        # authoritative reviewer record for R anywhere.
        self.assertNoRecordFor(harness, reviewer, role="reviewer")

        # 3. a SEPARATE terminal W is positively verified as the Worker, model-a.
        worker = self.session(harness, "worker")
        harness.verify_model_identity(
            "task_wkr", worker, role="worker", phase="implementation", attempt=1
        )
        self.assertEqual(
            harness._model_identity[("implementation", "worker")].resolved_model, "model-a"
        )

        # 4. THE DEFECT. A Worker delivery on W must not be admitted on the strength of
        #    the reviewer/model-b evidence, which no longer describes R. Pre-fix this
        #    succeeded, because model-b != model-a made the two look independent while
        #    both physical sessions were on model-a.
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.deliver(harness, worker, "worker")
        self.assertTrue(
            str(caught.exception).startswith(MODEL_SELECTION_PAIR_UNADMITTED),
            f"expected {MODEL_SELECTION_PAIR_UNADMITTED}, got: {caught.exception}",
        )
        self.assertNothingDelivered(recorder)

    def test_a_refused_delivery_stales_only_its_own_session(self) -> None:
        """Staling is keyed on the TERMINAL, and other sessions keep their evidence.

        The other half of fail-closed: a fix that cleared every record on any refusal
        would be "safe" and useless, because no pair could ever be admitted. Sessions this
        attempt asked nothing of were not switched, so their evidence still describes them.
        """
        # R is stable; W drifts on re-verification. The refusal therefore belongs to W's
        # session and R was never asked to select anything after its own verification.
        driver = sessions_resolving(["model-b"], ["model-a", "model-c"])
        _recorder, harness = self.harness_for(driver)
        reviewer = self.session(harness, "reviewer")
        worker = self.session(harness, "worker")
        harness.verify_model_identity(
            "task_rev", reviewer, role="reviewer", phase="implementation", attempt=1
        )
        harness.verify_model_identity(
            "task_wkr", worker, role="worker", phase="implementation", attempt=1
        )
        # Refuse on the WORKER's session: its resolved model drifts on re-verification.
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_model_identity(
                "task_wkr2", worker, role="worker", phase="implementation", attempt=2
            )
        self.assertNoRecordFor(harness, worker, role="worker")
        # The reviewer, on its own untouched session, is still authoritative.
        surviving = harness._model_identity.get(("implementation", "reviewer"))
        self.assertIsNotNone(surviving, "an untouched session's evidence was discarded")
        self.assertEqual(surviving.observed_at_terminal, reviewer)
        self.assertEqual(surviving.resolved_model, "model-b")
        self.assertIn(reviewer, harness._model_session_identity)

    def test_a_driver_that_raises_also_stales_the_session(self) -> None:
        """The strongest case, not the weakest: nobody can say whether it switched first.

        A driver that raises part-way through `select_and_verify()` leaves a session whose
        model this harness cannot name. Unknown is not "unchanged", so the records naming
        it are invalidated exactly as a reported refusal's are.
        """
        class Raising:
            def __init__(self) -> None:
                self.calls = 0

            def select_and_verify(self, ticket):
                self.calls += 1
                ticket.stamp()
                raise RuntimeError("the provider dropped the session mid-switch")

        driver = resolving_per_session("model-b")
        _recorder, harness = self.harness_for(driver)
        reviewer = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_rev", reviewer, role="reviewer", phase="implementation", attempt=1
        )
        self.assertIn(reviewer, harness._model_session_identity)
        raising = Raising()
        harness.model_driver = raising
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_model_identity(
                "task_rev2", reviewer, role="reviewer", phase="implementation", attempt=2
            )
        self.assertEqual(raising.calls, 1, "the driver must actually have been invoked")
        self.assertNoRecordFor(harness, reviewer, role="reviewer")


# ---- B1, half 1: the conflict is decided BEFORE the driver runs -------------------------

class PreSelectionRejectionTests(ModelSessionTestCase):
    """B1. A role/session conflict decidable from harness state alone must not reach the driver.

    Pre-fix failure mode: the M5 session check (`counterpart.observed_at_terminal ==
    terminal`) read nothing but harness state -- two strings, compared for equality -- yet
    sat ~220 lines BELOW `select_and_verify()`. So the literal reproduction route switched
    terminal R onto model-A and only then refused, manufacturing exactly the stale-evidence
    state B1 is about. The fix decides it before the ticket is even minted, which is why
    the assertion here is on the DRIVER CALL COUNT: a refusal proves the conflict was
    caught, but only an un-invoked driver proves the session was never mutated.
    """

    def test_the_second_role_on_one_session_never_reaches_the_driver(self) -> None:
        driver = sessions_resolving(["model-b"])
        recorder, harness = self.harness_for(driver)
        handle = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_rev", handle, role="reviewer", phase="implementation", attempt=1
        )
        after_first = self.switches(driver)
        self.assertEqual(after_first, 1, "the first verification must select once")

        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.verify_model_identity(
                "task_wkr", handle, role="worker", phase="implementation", attempt=1
            )
        message = str(caught.exception)
        self.assertTrue(
            message.startswith(REASON_WORKER_REVIEWER_MUST_DIFFER),
            f"expected {REASON_WORKER_REVIEWER_MUST_DIFFER}, got: {message}",
        )
        self.assertIn("one physical session cannot be both sides", message)
        # THE assertion. Unchanged between attempt 1 and attempt 2: no selection was
        # requested for the refused attempt, so no physical switch was attempted.
        self.assertEqual(
            self.switches(driver), after_first,
            "the driver was invoked for an attempt that harness state alone could "
            "refuse: the session was switched for a conflict decided afterwards, which "
            "is the window B1 lives in",
        )
        self.assertNothingDelivered(recorder)

    def test_no_ticket_or_ordinal_is_spent_on_the_refused_attempt(self) -> None:
        """The same fact read off the harness's own counter rather than the driver's log.

        `_model_selection_seq` only advances when a ticket's `stamp()` is called, so an
        unchanged counter is independent corroboration that nothing was requested -- and
        it also shows the refused attempt cannot burn the ordinal window a LATER legitimate
        attempt needs.
        """
        driver = sessions_resolving(["model-b"])
        _recorder, harness = self.harness_for(driver)
        handle = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_rev", handle, role="reviewer", phase="implementation", attempt=1
        )
        counter = harness._model_selection_seq
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_model_identity(
                "task_wkr", handle, role="worker", phase="implementation", attempt=1
            )
        self.assertEqual(harness._model_selection_seq, counter)

    def test_the_unswitched_session_keeps_its_evidence(self) -> None:
        """And therefore the delivery on a separate, legitimate session is still admitted.

        The positive counterpart to `StaleSessionEvidenceTests`, and the reason the hoist
        is the better fix: because the pre-selection refusal never touched R, R's reviewer
        evidence is still a true description of R, so it is NOT staled and the Worker's
        delivery on its own distinct session W proceeds. Fixing B1 by staling on every
        refusal would have broken this, and broken the capability OS-49 exists for.
        """
        driver = sessions_resolving(["model-b"], ["model-a"])
        recorder, harness = self.harness_for(driver)
        reviewer = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_rev", reviewer, role="reviewer", phase="implementation", attempt=1
        )
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_model_identity(
                "task_wkr", reviewer, role="worker", phase="implementation", attempt=1
            )
        # Round 2's M5 invariant, unchanged: the refused role records nothing and the
        # session keeps the role it was actually verified as.
        self.assertNotIn(("implementation", "worker"), harness._model_identity)
        self.assertEqual(harness._model_session_identity[reviewer][0], "reviewer")

        worker = self.session(harness, "worker")
        harness.verify_model_identity(
            "task_wkr2", worker, role="worker", phase="implementation", attempt=1
        )
        self.deliver(harness, worker, "worker")       # admitted: two distinct sessions
        self.assertNothingDelivered(recorder)


# ---- B1: invalidation is not a one-way trap --------------------------------------------

class SessionEvidenceRecoveryTests(ModelSessionTestCase):
    """B1. "...unless the physical session state is positively re-established."

    The review's required direction permits a staled session to become authoritative
    again, and names the only way: positive re-verification. A fix that staled
    irrecoverably would fail closed and also strand the run, so recovery is locked as a
    capability, not left as an accident.
    """

    def test_re_verifying_the_session_restores_pair_admission(self) -> None:
        # R: model-b (accepted), then model-a (drift -> refused, stales R), then model-b
        # again -- what a session re-selected onto its declared model looks like. W holds
        # model-a throughout, including on each DELIVERY, which selects again.
        driver = sessions_resolving(["model-b", "model-a", "model-b"], ["model-a"])
        _recorder, harness = self.harness_for(driver)
        reviewer = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_rev", reviewer, role="reviewer", phase="implementation", attempt=1
        )
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_model_identity(
                "task_rev2", reviewer, role="reviewer", phase="implementation", attempt=2
            )
        worker = self.session(harness, "worker")
        harness.verify_model_identity(
            "task_wkr", worker, role="worker", phase="implementation", attempt=1
        )
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.deliver(harness, worker, "worker")
        self.assertTrue(
            str(caught.exception).startswith(MODEL_SELECTION_PAIR_UNADMITTED)
        )

        # Positively re-establish the session: the driver selects and observes again.
        before = self.switches(driver)
        harness.verify_model_identity(
            "task_rev3", reviewer, role="reviewer", phase="implementation", attempt=3
        )
        self.assertEqual(
            self.switches(driver), before + 1,
            "recovery must rest on a fresh selection, not on a restored record",
        )
        restored = harness._model_identity[("implementation", "reviewer")]
        self.assertEqual(restored.observed_at_terminal, reviewer)
        self.assertEqual(restored.resolved_model, "model-b")
        self.deliver(harness, worker, "worker")       # now admitted

    def test_staling_clears_the_leg_i_history_so_a_retry_is_not_blocked(self) -> None:
        """Staling must not leave a record that refuses the very re-verification it needs.

        Leg (i) refuses a `(phase, role)` whose resolved model changed between accepted
        attempts. If staling dropped the session map but kept `_model_identity`, the
        recovery verification would hit leg (i) against the record it was meant to replace
        and the session could never be re-established -- a permanent stall dressed as
        fail-closed. The deletion is keyed on `observed_at_terminal`, which is what makes
        the two consistent.
        """
        driver = sessions_resolving(["model-b", "model-a", "model-a"])
        _recorder, harness = self.harness_for(driver)
        reviewer = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_rev", reviewer, role="reviewer", phase="implementation", attempt=1
        )
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_model_identity(
                "task_rev2", reviewer, role="reviewer", phase="implementation", attempt=2
            )
        # model-a now -- DIFFERENT from the model-b the staled record named. Pre-fix this
        # would have been refused as model_selection_ambiguous against a record that no
        # longer described anything.
        harness.verify_model_identity(
            "task_rev3", reviewer, role="reviewer", phase="implementation", attempt=3
        )
        self.assertEqual(
            harness._model_identity[("implementation", "reviewer")].resolved_model,
            "model-a",
        )


# ---- N1 --------------------------------------------------------------------------------

class TotalRollbackTests(ModelSessionTestCase):
    """N1. A rollback over a row that did not exist at snapshot time must leave no model cells.

    Pre-fix failure mode: `_model_evidence_snapshot()` stored `row_cells: None` when the
    handle had no ledger row, and `_restore_model_evidence()` read that as "nothing to
    undo" and returned early. But `start_worker()` calls `_attach_terminal()` AFTER the
    barrier, which for an unseen handle registers an adoption and then
    `_rebind_model_evidence()` writes the accepted evidence onto that brand-new row. A
    failure after that point restored the three maps and left the row advertising
    resolved_model/model_state/model_observed_at_dispatch for a delivery that never
    completed -- the contradictory state reuse condition 9 and the provenance row read.
    """

    def test_a_failure_after_adoption_leaves_no_model_cells_on_the_new_row(self) -> None:
        driver = resolving_per_session("model-b", "model-a")
        _recorder, harness = self.harness_for(driver)
        self.admit_counterpart(harness, "worker")     # the pair, on its own session
        unseen = "term_never_registered"
        self.assertNotIn(unseen, harness._terminals, "the premise is an UNSEEN handle")

        # The failure has to land AFTER `_attach_terminal()` has created the row, which is
        # the only window N1 exists in. `record_terminal_effect()` is the next statement
        # after it on the rung-3 path, so raising there is the minimal faithful stand-in
        # for the real failures that live past that point.
        def fail_after_adoption(*_args, **_kwargs):
            raise OrcaRuntimeError("post-adoption failure")

        harness.record_terminal_effect = fail_after_adoption
        with self.assertRaises(OrcaRuntimeError):
            harness.start_worker(
                "task_g", unseen, "spec", role="worker", phase="implementation", attempt=1
            )
        row = harness._terminals.get(unseen)
        self.assertIsNotNone(
            row, "the adoption row itself must survive: it is axis (c2) role/origin "
            "evidence that exists nowhere else, and the rollback did not create it",
        )
        self.assertEqual(
            {cell: row.get(cell) for cell in NO_MODEL_CELLS}, NO_MODEL_CELLS,
            "the newly-created row still advertises verified model state for a delivery "
            "that never completed",
        )
        # And the maps are rolled back as round 2 already required.
        self.assertNotIn(("implementation", "worker"), harness._model_identity)
        self.assertNotIn(unseen, harness._model_session_identity)
        self.assertNotIn(unseen, harness._model_pending_evidence)

    def test_the_cleared_cells_are_exactly_the_creation_time_shape(self) -> None:
        """Cleared, never deleted: the keys are read by name elsewhere.

        `register_terminal()` creates every model cell, and reuse condition 9 and the
        durable provenance row index them directly. Popping them would turn "this session
        has no verified model" into a KeyError, so the rollback writes the neutral values
        a fresh row carries -- and this asserts the two shapes are the same shape.
        """
        driver = resolving_per_session("model-b", "model-a")
        _recorder, harness = self.harness_for(driver)
        fresh = harness.register_terminal(
            "term_reference", role="external_or_adopted", origin="adopted"
        )
        self.assertEqual(
            {cell: fresh.get(cell) for cell in NO_MODEL_CELLS}, NO_MODEL_CELLS,
            "this test's own notion of a model-free row has drifted from "
            "register_terminal()'s",
        )
        self.assertEqual(
            set(NO_MODEL_CELLS), set(OrcaRuntimeHarness._MODEL_ROW_CELLS),
            "a model cell was added to the row without being added to the rollback",
        )
        self.assertEqual(
            OrcaRuntimeHarness._MODEL_ROW_CLEARED, NO_MODEL_CELLS,
            "the production cleared-shape and the creation-time shape disagree",
        )

    def test_a_pre_existing_row_is_still_restored_rather_than_cleared(self) -> None:
        """The round-2 behaviour on the other branch, unchanged.

        When the row DID exist at snapshot time, its cells are restored to their
        pre-barrier values -- which is not the same thing as cleared, and must not become
        it: a row whose model state an earlier, separate and successful verification
        legitimately earned keeps it.
        """
        driver = resolving_per_session("model-b", "model-a")
        _recorder, harness = self.harness_for(driver)
        self.admit_counterpart(harness, "worker")
        worker = self.session(harness, "worker")
        # An accepted pre-pass writes the row's model cells before `start_worker()` runs,
        # so this attempt's snapshot records real values rather than `None`.
        harness.verify_model_identity(
            "task_pre", worker, role="worker", phase="implementation", attempt=1
        )
        row = harness._terminals[worker]
        self.assertEqual(row["resolved_model"], "model-a")
        snapshot_shape = {cell: row.get(cell) for cell in NO_MODEL_CELLS}

        def fail_after_adoption(*_args, **_kwargs):
            raise OrcaRuntimeError("post-adoption failure")

        harness.record_terminal_effect = fail_after_adoption
        with self.assertRaises(OrcaRuntimeError):
            harness.start_worker(
                "task_g", worker, "spec", role="worker", phase="implementation", attempt=1
            )
        self.assertEqual(
            {cell: harness._terminals[worker].get(cell) for cell in NO_MODEL_CELLS},
            snapshot_shape,
            "a pre-existing row was cleared instead of restored",
        )


# ---- N2 --------------------------------------------------------------------------------

class NoDriverImportTests(unittest.TestCase):
    """N2. `FakeAdapter.capabilities()` with no model driver must not import agent_profile.

    Pre-fix failure mode: `capabilities()` called `_import_agent_profile()`
    unconditionally, then passed `self.model_driver` -- which is `None` on the production
    default and on the overwhelming majority of FakeAdapters -- to
    `model_selection_capabilities()`, whose documented answer for `None` is the empty set.
    So the default no-driver path took a cross-package import purely to be told "nothing",
    making it depend on `agent_profile` being importable in whatever layout it ran in.
    """

    def test_the_no_driver_path_does_not_import_agent_profile(self) -> None:
        """A FRESH subprocess, so no other test's imports can make this vacuous.

        In-process, `scripts.agent_profile` is already in `sys.modules` from this module's
        own imports, and a monkeypatch that bars it would be testing the bar rather than
        the adapter. A subprocess that imports `fake_adapter`, builds an adapter, calls
        `capabilities()` and THEN asks whether `agent_profile` ever arrived is the only
        honest form of the assertion.
        """
        program = textwrap.dedent(
            """
            import json, sys
            from scripts.deterministic_workflow.fake_adapter import FakeAdapter
            adapter = FakeAdapter([])
            caps = adapter.capabilities()
            print(json.dumps({
                "driver": adapter.model_driver,
                "imported": [
                    name for name in sys.modules
                    if name == "agent_profile" or name.endswith(".agent_profile")
                ],
                "declares_model_selection": "model_selection_verified" in caps,
            }))
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", program],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        observed = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertIsNone(observed["driver"], "the premise is a driverless adapter")
        self.assertEqual(
            observed["imported"], [],
            "capabilities() imported agent_profile on the no-driver path",
        )
        self.assertFalse(observed["declares_model_selection"])

    def test_a_driver_still_earns_the_capability_through_agent_profile(self) -> None:
        """The short-circuit declines exactly what the full predicate declines, no more.

        `model_selection_capabilities(None)` is the empty set, so `model_driver is None`
        and the predicate agree for a driverless adapter -- the skip is an optimization,
        not a second copy of the rule. A real driver must still go through
        `agent_profile`, which is what keeps this adapter, Gate A and Gate B unable to
        disagree about what counts as a driver (review M6).
        """
        from scripts.agent_profile import (
            MODEL_SELECTION_VERIFIED_CAPABILITY,
            model_selection_capabilities,
        )
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter

        self.assertEqual(model_selection_capabilities(None), frozenset())
        self.assertNotIn(
            MODEL_SELECTION_VERIFIED_CAPABILITY,
            FakeAdapter([]).capabilities(),
        )
        self.assertIn(
            MODEL_SELECTION_VERIFIED_CAPABILITY,
            FakeAdapter([], model_driver=InProcessModelDriver()).capabilities(),
        )
        # An object that EXISTS but cannot be called is not a driver, and the adapter must
        # still reach the one place that rule lives to find that out.
        self.assertNotIn(
            MODEL_SELECTION_VERIFIED_CAPABILITY,
            FakeAdapter([], model_driver=object()).capabilities(),
        )


# ---- R1: a BaseException exit from the driver also invalidates the evidence ------------

class InterruptedSelectionTests(ModelSessionTestCase):
    """R1. A driver that begins selecting and then raises `BaseException` stales too.

    Pre-fix failure mode: the handler was `except Exception`, and the staling call lived
    INSIDE the branch that handler fed. `KeyboardInterrupt` and `SystemExit` derive from
    `BaseException`, not `Exception`, so they propagated straight out of
    `_verify_model_identity()` -- the `finally:` revoked the ticket, but the branch holding
    `_stale_model_evidence()` was never entered. The driver had already called
    `ticket.stamp()`, so selection provably BEGAN and the physical session may have been
    switched, yet `_model_identity[(phase, role)]`, `_model_session_identity[terminal]`,
    `_model_pending_evidence[terminal]` and the terminal's ledger row all still advertised
    the OLD verified model. A later same-command delivery then read that record as proof
    of independence -- B1's consequence, reached through a third route.

    The fix separates the two concerns: invalidation keys on the SIDE EFFECT (selection may
    have occurred) and so runs for every `BaseException`, while NORMALIZATION still keys on
    `Exception` and leaves interrupt/finality semantics alone. These tests assert both
    halves, because a "fix" that swallowed `KeyboardInterrupt` into
    `MODEL_SELECTION_UNVERIFIED` would pass a staling-only assertion while breaking
    Ctrl-C.
    """

    def _interrupted_scenario(self, exception_type):
        """R verified as model-b, then a selection on R stamps and raises `exception_type`.

        Returns everything the three assertions need. The driver is restored afterwards so
        the FOLLOW-UP delivery -- assertion (c), and the whole point -- can run.
        """
        driver = sessions_resolving(["model-b"], ["model-a"])
        recorder, harness = self.harness_for(driver)

        # 1. the Reviewer is positively verified on terminal R as model-b.
        reviewer = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_rev", reviewer, role="reviewer", phase="implementation", attempt=1
        )
        self.assertEqual(
            harness._model_identity[("implementation", "reviewer")].resolved_model,
            "model-b",
            "the premise is a POSITIVELY VERIFIED record for this session",
        )
        self.assertIn(reviewer, harness._model_session_identity)

        class Interrupting:
            """Stamps FIRST, then raises: selection provably began before the exception."""

            def __init__(self) -> None:
                self.calls = 0
                self.stamped: list[int] = []

            def select_and_verify(self, ticket):
                self.calls += 1
                self.stamped.append(ticket.stamp())
                raise exception_type("the session was interrupted mid-switch")

        interrupting = Interrupting()
        harness.model_driver = interrupting
        before_seq = harness._model_selection_seq

        # 2. the SAME session R is re-verified and the driver is interrupted mid-switch.
        with self.assertRaises(exception_type) as caught:
            harness.verify_model_identity(
                "task_rev2", reviewer, role="reviewer", phase="implementation", attempt=2
            )
        self.assertEqual(
            interrupting.calls, 1, "the driver must actually have been invoked"
        )
        self.assertEqual(
            len(interrupting.stamped), 1,
            "the premise is that the SIDE EFFECT began: an ordinal was drawn before the "
            "exception, so this harness cannot claim the session was never touched",
        )
        self.assertGreater(harness._model_selection_seq, before_seq)
        harness.model_driver = driver          # restore, for the follow-up delivery
        return recorder, harness, reviewer, caught.exception

    def _assert_propagated_unchanged(self, exception_type, raised) -> None:
        """(a) It came out as ITSELF -- neither normalized nor swallowed."""
        self.assertIsInstance(raised, exception_type)
        self.assertNotIsInstance(
            raised, OrcaRuntimeError,
            f"{exception_type.__name__} was normalized into the model-selection "
            "vocabulary; interrupt and finality semantics must keep propagating as "
            "themselves, and only ordinary Exceptions become "
            "model_selection_unverified",
        )
        self.assertIn("interrupted mid-switch", str(raised))

    def _assert_stale_evidence_cannot_admit_a_delivery(
        self, recorder, harness, reviewer
    ) -> None:
        """(c) The POINT. A later Worker delivery must not lean on the dead evidence.

        Not the immediate exception -- that was never the bug. Pre-fix the surviving
        reviewer/model-b record made the Worker's model-a look independent of it while
        both physical sessions may have sat on one model, and the delivery was admitted.
        """
        worker = self.session(harness, "worker")
        harness.verify_model_identity(
            "task_wkr", worker, role="worker", phase="implementation", attempt=1
        )
        self.assertEqual(
            harness._model_identity[("implementation", "worker")].resolved_model,
            "model-a",
        )
        with self.assertRaises(OrcaRuntimeError) as refused:
            self.deliver(harness, worker, "worker")
        self.assertTrue(
            str(refused.exception).startswith(MODEL_SELECTION_PAIR_UNADMITTED),
            f"expected {MODEL_SELECTION_PAIR_UNADMITTED}, got: {refused.exception}",
        )
        self.assertNothingDelivered(recorder)

    def test_a_keyboard_interrupt_mid_selection_stales_the_session(self) -> None:
        recorder, harness, reviewer, raised = self._interrupted_scenario(
            KeyboardInterrupt
        )
        self._assert_propagated_unchanged(KeyboardInterrupt, raised)
        # (b) nothing anywhere still describes R as verified -- all four recording sites.
        self.assertNoRecordFor(harness, reviewer, role="reviewer")
        self._assert_stale_evidence_cannot_admit_a_delivery(recorder, harness, reviewer)

    def test_a_system_exit_mid_selection_stales_the_session(self) -> None:
        recorder, harness, reviewer, raised = self._interrupted_scenario(SystemExit)
        self._assert_propagated_unchanged(SystemExit, raised)
        self.assertNoRecordFor(harness, reviewer, role="reviewer")
        self._assert_stale_evidence_cannot_admit_a_delivery(recorder, harness, reviewer)

    def test_the_ticket_is_still_revoked_when_the_driver_is_interrupted(self) -> None:
        """The M3-era `finally:` guarantee, unchanged by the R1 restructuring.

        The fix nests the revoke in an inner `finally:` so that invalidation can wrap it,
        and the ordering it protects must survive that: an interrupted driver cannot leave
        a live stamp window behind for the NEXT attempt to spend.
        """
        for exception_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception=exception_type.__name__):
                _recorder, harness, _reviewer, _raised = self._interrupted_scenario(
                    exception_type
                )
                self.assertEqual(
                    harness._model_selection_open_tokens, set(),
                    "an interrupted driver left a live stamp window open",
                )

    def test_an_ordinary_exception_is_still_normalized_unchanged(self) -> None:
        """The other side of the separation: `RuntimeError` behaviour did NOT move.

        R1's fix widens INVALIDATION to `BaseException` while leaving NORMALIZATION on
        `Exception`. If the two had been widened together, an ordinary raising driver
        would now propagate `RuntimeError` out of the barrier instead of the closed
        vocabulary member -- so this pins the member, and the message content, in place.
        """
        driver = resolving_per_session("model-b")
        _recorder, harness = self.harness_for(driver)
        reviewer = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_rev", reviewer, role="reviewer", phase="implementation", attempt=1
        )

        class Raising:
            def select_and_verify(self, ticket):
                ticket.stamp()
                raise RuntimeError("the provider dropped the session mid-switch")

        harness.model_driver = Raising()
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.verify_model_identity(
                "task_rev2", reviewer, role="reviewer", phase="implementation", attempt=2
            )
        message = str(caught.exception)
        self.assertTrue(
            message.startswith(MODEL_SELECTION_UNVERIFIED),
            f"expected {MODEL_SELECTION_UNVERIFIED}, got: {message}",
        )
        self.assertIn(
            "the model-selection driver raised RuntimeError: the provider dropped the "
            "session mid-switch", message,
        )
        self.assertNoRecordFor(harness, reviewer, role="reviewer")


# ---- R2: the missing same-command counterpart is decided BEFORE the driver -------------

class PairAdmissionPreSelectionTests(ModelSessionTestCase):
    """R2. A delivery already known inadmissible must not physically switch its session.

    Pre-fix failure mode: `counterpart is None`, `require_pair_admission`, the counterpart
    entry's `required`/`resolved`/`command` and this call's `command` are ALL harness state
    or parameters of the call -- not one of them is produced by the driver -- yet the
    branch reading them sat at the very END of `_verify_model_identity()`, below
    `select_and_verify()`. So a same-command delivery whose counterpart held no verified
    evidence minted a ticket, drew ordinals and asked the driver to SWITCH THE SESSION,
    and only then refused with `model_selection_pair_unadmitted`. The refusal was right;
    the mutation before it was the defect, and it is the same window B1 lives in.

    The assertions are therefore on the four things that must NOT have happened, exactly
    as `PreSelectionRejectionTests` does for the session half: a refusal alone proves only
    that the conflict was caught, while an un-invoked driver and an unmoved ordinal counter
    prove the session was never touched.
    """

    def test_an_unadmitted_same_command_delivery_never_reaches_the_driver(self) -> None:
        driver = sessions_resolving(["model-a"])
        recorder, harness = self.harness_for(driver)
        worker = self.session(harness, "worker")
        self.assertEqual(
            harness._model_identity, {},
            "the premise is a pair whose counterpart holds NO verified evidence",
        )
        before_calls = self.switches(driver)
        before_seq = harness._model_selection_seq
        row_before = {
            cell: harness._terminals[worker].get(cell) for cell in NO_MODEL_CELLS
        }
        # NOT compared against NO_MODEL_CELLS: `create_fake_terminal()` already stamped
        # `model_state: requested` from the ROUTING DECLARATION, which is not evidence and
        # did not become false. What must not move is the evidence, so the assertion below
        # is that the row is UNCHANGED -- mutation == 0 -- rather than neutral.
        self.assertEqual(
            row_before["resolved_model"], "",
            "the premise is a session with no POSITIVELY VERIFIED model",
        )

        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.start_worker(
                "task_g", worker, "spec", role="worker", phase="implementation",
                attempt=1,
            )
        message = str(caught.exception)
        self.assertTrue(
            message.startswith(MODEL_SELECTION_PAIR_UNADMITTED),
            f"expected {MODEL_SELECTION_PAIR_UNADMITTED}, got: {message}",
        )
        # The message content is unmoved, so no caller's expectation moves with the hoist.
        self.assertIn("shares this command 'claude'", message)
        self.assertIn("alias onto one model", message)

        # 1. driver `select_and_verify` call count == 0.
        self.assertEqual(
            self.switches(driver), before_calls,
            "the driver was asked to select for a delivery that harness state alone "
            "could refuse: the session was switched for a conflict decided afterwards, "
            "which is the window R2 is about",
        )
        self.assertEqual(before_calls, 0, "no selection should have happened at all")
        # 2. tickets minted / ordinals consumed == 0. Minting ITSELF advances the counter,
        #    so an unmoved counter proves no ticket was even issued, let alone stamped.
        self.assertEqual(harness._model_selection_seq, before_seq)
        self.assertEqual(harness._model_selection_open_tokens, set())
        # 3. session model mutation == 0.
        self.assertEqual(
            {cell: harness._terminals[worker].get(cell) for cell in NO_MODEL_CELLS},
            row_before,
            "the refused delivery still mutated the session's model state",
        )
        self.assertNotIn(worker, harness._model_session_identity)
        self.assertNotIn(worker, harness._model_pending_evidence)
        self.assertEqual(harness._model_identity, {})
        # 4. delivery verbs issued == 0.
        self.assertNothingDelivered(recorder)

    def test_the_refusal_is_symmetric_across_the_two_roles(self) -> None:
        """Not a courtesy: the rule is on the FIRST delivery of EITHER role, so it cannot
        be satisfied by always dispatching one of them first."""
        for role in ("worker", "reviewer"):
            with self.subTest(role=role):
                driver = sessions_resolving(["model-a"])
                recorder, harness = self.harness_for(driver)
                handle = self.session(harness, role)
                with self.assertRaises(OrcaRuntimeError) as caught:
                    harness.start_worker(
                        "task_g", handle, "spec", role=role, phase="implementation",
                        attempt=1,
                    )
                self.assertTrue(
                    str(caught.exception).startswith(MODEL_SELECTION_PAIR_UNADMITTED)
                )
                self.assertEqual(self.switches(driver), 0)
                self.assertEqual(harness._model_selection_seq, 0)
                self.assertNothingDelivered(recorder)

    def test_the_pre_pass_still_bootstraps_the_first_role_of_a_pair(self) -> None:
        """THE SCOPING TEST. `require_pair_admission=False` must still verify role one.

        Without this, a future change could "fix" R2 by gating the new check on nothing
        and the suite would not notice -- yet it would make same-command pairs
        UNROUTABLE, because the pre-pass is how a caller bootstraps one and the FIRST role
        it verifies necessarily has no counterpart evidence yet. The condition would be
        unsatisfiable by construction, which is a fail-closed-shaped way of deleting the
        capability OS-49 exists for.

        So this drives the whole legitimate bootstrap: pre-pass role one with no
        counterpart evidence at all, pre-pass role two, then DELIVER.
        """
        driver = sessions_resolving(["model-b"], ["model-a"])
        recorder, harness = self.harness_for(driver)
        reviewer = self.session(harness, "reviewer")
        self.assertEqual(
            harness._model_identity, {},
            "the premise is that the counterpart has no evidence when role one verifies",
        )

        # Role ONE, through the public pre-pass. No counterpart evidence exists.
        harness.verify_model_identity(
            "task_rev", reviewer, role="reviewer", phase="implementation", attempt=1
        )
        record = harness._model_identity[("implementation", "reviewer")]
        self.assertEqual(record.resolved_model, "model-b")
        self.assertEqual(record.observed_at_terminal, reviewer)
        self.assertEqual(
            self.switches(driver), 1,
            "the pre-pass must really have selected: a short-circuited bootstrap records "
            "nothing and proves nothing",
        )

        # Role TWO, likewise, and only now is the pair admitted.
        worker = self.session(harness, "worker")
        harness.verify_model_identity(
            "task_wkr", worker, role="worker", phase="implementation", attempt=1
        )
        self.assertEqual(
            harness._model_identity[("implementation", "worker")].resolved_model,
            "model-a",
        )

        # And the DELIVERY the bootstrap exists to enable goes through.
        self.deliver(harness, worker, "worker")
        harness.start_worker(
            "task_g", worker, "spec", role="worker", phase="implementation", attempt=1
        )

    def test_a_distinct_command_pair_is_untouched_by_the_hoist(self) -> None:
        """Row 1 of the effective-identity rule: distinct commands are independent alone.

        The hoisted check is scoped to SAME-command pairs, so a distinct-command delivery
        must still reach the driver and be admitted with no counterpart evidence at all --
        the pre-OS-49 lifecycle, unchanged.
        """
        recorder = SequentialTerminalExec()
        driver = resolving_per_session("model-a")
        harness = self.build(
            recorder,
            routing=routing_from(DISTINCT_COMMAND_PROFILE, "distinct"),
            model_driver=driver,
        )
        worker = harness.create_fake_terminal(
            "worker", "complete", iteration=1, phase="implementation"
        )
        self.assertEqual(harness._model_identity, {})
        harness.start_worker(
            "task_g", worker, "spec", role="worker", phase="implementation", attempt=1
        )
        self.assertEqual(
            harness._model_identity[("implementation", "worker")].resolved_model,
            "model-a",
            "a distinct-command delivery was refused or never recorded: the hoist leaked "
            "outside the same-command scope it is defined on",
        )


if __name__ == "__main__":
    unittest.main()
