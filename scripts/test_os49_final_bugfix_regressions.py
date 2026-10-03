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

import ast
import contextlib
import json
import signal
import subprocess
import sys
import textwrap
import threading
import time
import unittest
from pathlib import Path

from scripts.agent_profile import REASON_WORKER_REVIEWER_MUST_DIFFER
from scripts.deterministic_workflow.fake_adapter import InProcessModelDriver
from scripts.orca_runtime_harness import (
    MODEL_SELECTION_AMBIGUOUS,
    MODEL_SELECTION_PAIR_UNADMITTED,
    MODEL_SELECTION_UNVERIFIED,
    UNRENDERABLE_TEXT,
    UNRENDERABLE_TYPE_NAME,
    ModelEvidence,
    OrcaRuntimeError,
    OrcaRuntimeHarness,
    safe_exception_text,
    safe_text,
    safe_type_name,
)
from scripts.test_orca_runtime_contract import SequentialTerminalExec
from scripts.test_os49_bugfix_regressions import (
    _verified_resolver,
    resolving_per_session,
)
from scripts.test_os49_delivery_barrier import (
    BarrierTestCase,
    DISTINCT_COMMAND_PROFILE,
    RecordingDriver,
    SPLIT_PROFILE,
    conforming,
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

    def test_staling_does_not_erase_the_history_a_drift_retry_would_need_erased(self) -> None:
        """B2, THE regression. Retrying the drifted model after a drift refusal is REFUSED.

        This test replaces `test_staling_clears_the_leg_i_history_so_a_retry_is_not_blocked`,
        which asserted the OPPOSITE on this exact driver sequence and was WRONG. The
        behaviour it locked in was B2 itself:

            session S:  alias-X -> model-b   accepted
            same S:     alias-X -> model-a   refused as drift
            retry on S: alias-X -> model-a   was ACCEPTED

        The old test read the third step as "recovery" and its docstring argued that
        refusing it would be "a permanent stall dressed as fail-closed". That reading
        confuses two different things. The first refusal did not merely fail -- it DELETED
        the only records that knew S had ever resolved to model-b, so the retry was
        compared against nothing and admitted the very drift that had just been refused. A
        drift refusal became the way to launder a drifting session into an accepted one,
        which inverts the invariant the refusal exists to enforce: two refusals in a row
        would have been fail-closed, and instead the first refusal cleared the ground for
        the second attempt.

        Nor is the refusal a stall. OS-49's own stated principle is that a session whose
        resolved model changed is not the agent that was verified, so there is nothing on S
        left to recover -- S is spent, and the remedy is a NEW session, which every caller
        can create for the price of one `terminal create`. "Permanent" would require the
        work to become unroutable, and it does not: nothing about a fresh terminal is
        blocked, and the genuine recovery route is still open and still tested, by
        `test_re_verifying_the_session_restores_pair_admission` -- a retry that resolves
        BACK to S's baseline, which remains accepted and is unchanged by this round.

        Driver sequence IDENTICAL to the old test's, so the only thing that moved is the
        verdict on attempt 3.
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
        # Authority is gone -- that part was and remains correct.
        self.assertNoRecordFor(harness, reviewer, role="reviewer")
        # HISTORY is not, which is the fix. Both baselines survive the staling.
        self.assertEqual(
            harness._model_session_history.get(reviewer),
            ("reviewer", "implementation", "model-b"),
        )
        self.assertEqual(
            harness._model_role_history.get(("implementation", "reviewer")), "model-b"
        )
        # model-a again -- the SAME drifted model attempt 2 was refused for. Refused.
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.verify_model_identity(
                "task_rev3", reviewer, role="reviewer", phase="implementation", attempt=3
            )
        message = str(caught.exception)
        self.assertTrue(
            message.startswith(MODEL_SELECTION_AMBIGUOUS),
            f"expected {MODEL_SELECTION_AMBIGUOUS}, got: {message}",
        )
        self.assertIn("model-b", message)   # the baseline is named in the diagnosis
        # And no authority was created by the refused retry.
        self.assertNoRecordFor(harness, reviewer, role="reviewer")

    def test_a_fresh_session_is_the_remedy_and_is_not_blocked(self) -> None:
        """The other half of "this is not a permanent stall", asserted rather than argued.

        The coordinator's ruling on B2 rests on the claim that refusing a drift retry
        strands nothing, because a NEW session is available. If that claim were false the
        ruling would be wrong, so it is tested: after S is spent by a drift refusal, the
        same role verifies on a fresh terminal and the run continues.

        The fresh session must resolve to the ROLE's baseline, which is the correct scope of
        leg (i): the routing's declared alias for that role is frozen for the run, so the
        role resolving to a second model mid-run is drift no matter which session it happens
        on. Changing a role's model is a new RUN's business -- which is exactly why the
        history is run-scoped.
        """
        # S: model-b accepted, then model-a (refused). The fresh session resolves model-b,
        # the role's baseline. W holds model-a so the pair stays independent.
        driver = sessions_resolving(
            ["model-b", "model-a"], ["model-a"], ["model-b"]
        )
        _recorder, harness = self.harness_for(driver)
        spent = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_rev", spent, role="reviewer", phase="implementation", attempt=1
        )
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_model_identity(
                "task_rev2", spent, role="reviewer", phase="implementation", attempt=2
            )
        worker = self.session(harness, "worker")
        harness.verify_model_identity(
            "task_wkr", worker, role="worker", phase="implementation", attempt=1
        )
        # A brand-new terminal for the Reviewer. No history names it, so nothing blocks it.
        fresh = self.session(harness, "reviewer")
        self.assertNotEqual(fresh, spent)
        harness.verify_model_identity(
            "task_rev3", fresh, role="reviewer", phase="implementation", attempt=3
        )
        restored = harness._model_identity[("implementation", "reviewer")]
        self.assertEqual(restored.observed_at_terminal, fresh)
        self.assertEqual(restored.resolved_model, "model-b")
        # And the run goes on: the pair is admitted again.
        self.deliver(harness, worker, "worker")

    def test_the_spent_session_stays_refused_for_the_rest_of_the_run(self) -> None:
        """History is append-only for the life of the run, so the refusal does not decay.

        A fix that kept the history only until the next attempt, or that let a successful
        verification on ANOTHER session clear it, would reopen B2 one step further out. The
        baseline for a spent session survives both.
        """
        # Sequences are assigned in FIRST-SEEN terminal order: `spent` takes the first,
        # `fresh` the second. `fresh` resolves the role's baseline so the only thing under
        # test is whether `spent` stays refused.
        driver = sessions_resolving(
            ["model-b", "model-a", "model-a", "model-a"], ["model-b"]
        )
        _recorder, harness = self.harness_for(driver)
        spent = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_rev", spent, role="reviewer", phase="implementation", attempt=1
        )
        for attempt in (2, 3):
            with self.assertRaises(OrcaRuntimeError) as caught:
                harness.verify_model_identity(
                    f"task_rev{attempt}", spent, role="reviewer",
                    phase="implementation", attempt=attempt,
                )
            self.assertTrue(str(caught.exception).startswith(MODEL_SELECTION_AMBIGUOUS))
        # A successful verification elsewhere does not launder the spent session either.
        fresh = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_fresh", fresh, role="reviewer", phase="implementation", attempt=4
        )
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.verify_model_identity(
                "task_rev4", spent, role="reviewer", phase="implementation", attempt=5
            )
        self.assertTrue(str(caught.exception).startswith(MODEL_SELECTION_AMBIGUOUS))

    def test_the_history_is_run_scoped_and_a_new_run_may_resolve_differently(self) -> None:
        """Append-only "for the life of the RUN", not forever -- the other end of B2's fix.

        A history that outlived its run would turn B2's fix into a different defect: a run
        legitimately declaring a different model would be refused by a baseline no session
        in it produced, which is B1 wearing B2's clothes. The run boundary is what bounds
        it.
        """
        driver = sessions_resolving(["model-b", "model-a", "model-a"])
        recorder, harness = self.harness_for(driver)
        recorder.results["run-create"] = {"run": {"id": "run_hist_one"}}
        harness.start_run("history one", requested_phases=("implementation",))
        session = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_rev", session, role="reviewer", phase="implementation", attempt=1
        )
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_model_identity(
                "task_rev2", session, role="reviewer", phase="implementation", attempt=2
            )
        self.assertIn(session, harness._model_session_history)
        recorder.results["run-create"] = {"run": {"id": "run_hist_two"}}
        harness.start_run("history two", requested_phases=("implementation",))
        self.assertEqual(harness._model_session_history, {})
        self.assertEqual(harness._model_role_history, {})
        # model-a, which run 1 refused. A NEW run is entitled to it.
        harness.verify_model_identity(
            "task_rev3", session, role="reviewer", phase="implementation", attempt=1
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


# ---- B1: the RUN BOUNDARY itself -------------------------------------------------------

class RunBoundaryModelStateTests(ModelSessionTestCase):
    """B1. A previous run's model evidence must never be readable by the next run.

    Pre-fix failure mode: `start_run()` reset `_signals`, `_ledger`, `_deliveries`,
    `_deliveries_restored_for`, `_last_settled`, `_pending_verification`, `_run_started_at`
    and `_timing` -- and none of the three model maps. Only `finish()` cleared those, and
    `finish()` is the CLEAN exit: a run that fails, raises, or is abandoned never reaches
    it. One harness instance driving several runs in sequence is the normal shape (
    `run_runtime_scenarios()` drives A..I on one instance), so run 1's records stayed live
    into run 2.

    The F-002 run filter did not cover it. That filter is in `_counterpart_model_identity()`
    and applies to the COUNTERPART read only; `_model_session_identity` is keyed on the
    TERMINAL and `_model_pending_evidence` carries no run scope at all, so leg (k) read a
    previous run's resolved model as this run's drift baseline and REFUSED a legitimate
    verification -- the leak's first consequence is a false refusal, not a false admission.

    Every test here drives the REAL `start_run()` twice with NO `finish()` between, which is
    the review's stated reproduction shape.
    """

    def two_runs(self, driver):
        recorder, harness = self.harness_for(driver)
        recorder.results["run-create"] = {"run": {"id": "run_boundary_one"}}
        harness.start_run("boundary one", requested_phases=("implementation",))
        return recorder, harness

    def next_run(self, recorder, harness):
        """The second run boundary, with run 1 deliberately NOT finished."""
        recorder.results["run-create"] = {"run": {"id": "run_boundary_two"}}
        harness.start_run("boundary two", requested_phases=("implementation",))

    def test_a_legitimate_second_run_verification_is_not_refused_by_run_one(self) -> None:
        """THE reproduction. Run 1 does not finish; run 2 re-verifies the same session.

        The session resolves a DIFFERENT model in run 2, which is legitimate -- a new run
        materializes its own routing and is entitled to its own resolved values. Pre-fix
        leg (k) compared it against run 1's leaked record and refused it as
        `model_selection_ambiguous`.
        """
        driver = sessions_resolving(["model-a", "model-b"])
        recorder, harness = self.two_runs(driver)
        session = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_r1", session, role="reviewer", phase="implementation", attempt=1
        )
        self.assertIn(session, harness._model_session_identity)

        self.next_run(recorder, harness)
        # Run 2, same physical session, resolving model-b. Must be ACCEPTED.
        harness.verify_model_identity(
            "task_r2", session, role="reviewer", phase="implementation", attempt=1
        )
        accepted = harness._model_identity[("implementation", "reviewer")]
        self.assertEqual(accepted.resolved_model, "model-b")
        self.assertEqual(accepted.observed_at_run, "run_boundary_two")

    def test_the_boundary_clears_every_piece_of_run_scoped_model_state(self) -> None:
        """All five maps, read off the harness after the real boundary ran.

        Asserted as a GROUP rather than one map at a time: the defect was a map being
        forgotten, so the test that catches the next forgotten map has to name the whole
        set.
        """
        driver = sessions_resolving(["model-a", "model-b"])
        recorder, harness = self.two_runs(driver)
        session = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_r1", session, role="reviewer", phase="implementation", attempt=1
        )
        # Every map carries run 1 state.
        populated = {
            "_model_identity": harness._model_identity,
            "_model_pending_evidence": harness._model_pending_evidence,
            "_model_session_identity": harness._model_session_identity,
            "_model_role_history": harness._model_role_history,
            "_model_session_history": harness._model_session_history,
        }
        for name, mapping in populated.items():
            self.assertNotEqual(mapping, {}, f"{name} was never populated by run 1")

        self.next_run(recorder, harness)
        for name in populated:
            self.assertEqual(
                getattr(harness, name), {},
                f"{name} survived the run boundary: run 1's model evidence is readable "
                "by run 2, which can refuse a valid verification or admit an invalid pair",
            )

    def test_run_one_cannot_admit_a_run_two_pair(self) -> None:
        """The leak's other consequence: a leaked counterpart granting pair admission.

        F-002's filter already refuses a leaked COUNTERPART, so this asserts the behaviour
        is unchanged by the reset -- the delivery is refused as PAIR_UNADMITTED rather than
        admitted on run 1's reviewer record -- and that the reset did not make the refusal
        depend on that filter alone.
        """
        driver = sessions_resolving(["model-b"], ["model-a"])
        recorder, harness = self.two_runs(driver)
        reviewer = self.session(harness, "reviewer")
        worker = self.session(harness, "worker")
        harness.verify_model_identity(
            "task_rev", reviewer, role="reviewer", phase="implementation", attempt=1
        )
        self.next_run(recorder, harness)
        # Run 2 verifies ONLY the worker. The reviewer's run-1 record must not admit it.
        harness.verify_model_identity(
            "task_wkr", worker, role="worker", phase="implementation", attempt=1
        )
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.deliver(harness, worker, "worker")
        self.assertTrue(
            str(caught.exception).startswith(MODEL_SELECTION_PAIR_UNADMITTED),
            f"expected {MODEL_SELECTION_PAIR_UNADMITTED}, got: {caught.exception}",
        )

    def test_the_reset_is_at_the_boundary_and_not_only_on_the_clean_exit(self) -> None:
        """Read off the real sources: `start_run()` is what every run passes through.

        `finish()` keeping its copy is correct and deliberate, so both are required. A fix
        that moved the statements instead of adding them would leave the interval after a
        clean `finish()` carrying live records, and a fix that only kept `finish()` is the
        defect.
        """
        import inspect

        maps = (
            "self._model_identity = {}",
            "self._model_pending_evidence = {}",
            "self._model_session_identity = {}",
            "self._model_role_history = {}",
            "self._model_session_history = {}",
        )
        for boundary in (OrcaRuntimeHarness.start_run, OrcaRuntimeHarness.finish):
            source = inspect.getsource(boundary)
            for reset in maps:
                self.assertIn(
                    reset, source, f"{boundary.__name__}() does not clear {reset!r}"
                )


# ---- B3: the POST-SELECTION VALIDATION BOUNDARY ----------------------------------------

class MalformedEvidenceFieldEvidence(ModelEvidence):
    """A `ModelEvidence` whose `requested_model` RAISES when a validation leg reads it.

    Passes `isinstance(evidence, ModelEvidence)`, so it reaches every leg -- which is the
    point: B3 is not about one field's type, it is about any leg being able to raise. Built
    through `object.__new__` because the frozen dataclass `__init__` cannot assign through a
    property, and a driver that returns a malformed object is not obliged to have built it
    the way the dataclass intends.
    """

    MESSAGE = "a generic post-selection validation failure"

    @property
    def requested_model(self):                       # type: ignore[override]
        raise RuntimeError(self.MESSAGE)

    @classmethod
    def around(cls, base: ModelEvidence) -> "MalformedEvidenceFieldEvidence":
        import dataclasses

        obj = object.__new__(cls)
        for field in dataclasses.fields(ModelEvidence):
            if field.name != "requested_model":
                object.__setattr__(obj, field.name, getattr(base, field.name))
        return obj


class PostSelectionValidationBoundaryTests(ModelSessionTestCase):
    """B3. Once selection has executed, NO unsuccessful exit may leave evidence authoritative.

    Pre-fix failure mode: the `try/except BaseException` added in iteration 2 wrapped ONLY
    the `select_and_verify()` call, and `refuse()` covered only the legs that go THROUGH it.
    Every validation leg ran outside any handler, so a leg that RAISED instead of refusing
    propagated straight out with no staling at all. `ModelEvidence` is a plain frozen
    dataclass with no field validation, so a driver may return any type in any field:
    `MODEL_TOKEN_PATTERN.fullmatch(123)` raises `TypeError: expected string or bytes-like
    object, got 'int'` and the session kept an authoritative record the driver had already
    invalidated by switching it.

    These tests assert the BOUNDARY, not the one example. The parametrized shapes below
    cover several malformed field types, and `test_a_generic_post_selection_exception...`
    covers a leg raising for a reason that has nothing to do with `resolved_model` -- which
    is what stops this from being re-fixed one site at a time.
    """

    #: Malformed `resolved_model` shapes, each of which made a DIFFERENT leg raise a
    #: different `TypeError` out of the regex check.
    MALFORMED_RESOLVED = (
        ("int", 123),
        ("bytes", b"model-a"),
        ("list", ["model-a"]),
        ("dict", {"model": "a"}),
        ("none", None),
        ("float", 5.2),
        ("bool", True),
        ("tuple", ("model-a",)),
    )

    def staled_harness(self, make_evidence):
        """Verify a session, then re-verify it with a driver returning malformed evidence.

        Returns (harness, session, raised). The first verification is what gives the session
        an authoritative record for the second one to have to invalidate.
        """
        _recorder, harness = self.harness_for(resolving_per_session("model-a"))
        session = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_one", session, role="reviewer", phase="implementation", attempt=1
        )
        self.assertIn(session, harness._model_session_identity)
        harness.model_driver = RecordingDriver(make_evidence)
        raised = None
        try:
            harness.verify_model_identity(
                "task_two", session, role="reviewer", phase="implementation", attempt=2
            )
        except BaseException as exc:                  # noqa: BLE001 - that is the subject
            raised = exc
        else:                                         # pragma: no cover - a failure
            self.fail("malformed evidence was ACCEPTED")
        return harness, session, raised

    def test_every_malformed_resolved_model_type_is_refused_not_raised(self) -> None:
        """Malformed TYPED FIELDS, as a closed set rather than as one example.

        Two assertions per shape, and both matter. The refusal is in the closed OS-49
        vocabulary, so a caller can branch on it -- pre-fix every one of these was a raw
        `TypeError`, which is outside the vocabulary by construction. And the session is no
        longer authoritative, which is the invariant itself.
        """
        for label, value in self.MALFORMED_RESOLVED:
            with self.subTest(resolved_model=label):
                # `resolved_model=` rather than `resolved=`: the latter treats `None`
                # as "use the ticket's requested model", which would silently drop the
                # most interesting shape in the set.
                harness, session, raised = self.staled_harness(
                    lambda t, rq, ob, v=value: conforming(t, rq, ob, resolved_model=v)
                )
                self.assertIsInstance(
                    raised, OrcaRuntimeError,
                    f"resolved_model={label} escaped the closed vocabulary as "
                    f"{type(raised).__name__}",
                )
                self.assertTrue(
                    str(raised).startswith(MODEL_SELECTION_UNVERIFIED),
                    f"expected {MODEL_SELECTION_UNVERIFIED}, got: {raised}",
                )
                self.assertNoRecordFor(harness, session, role="reviewer")

    def test_a_generic_post_selection_exception_also_invalidates(self) -> None:
        """The GENERIC case -- a leg raising for a reason unrelated to `resolved_model`.

        This is what makes the fix a boundary rather than a patch. The evidence here reaches
        the `requested_model` echo leg (g), which is a plain inequality that no amount of
        type-checking `resolved_model` would protect, and reading the field raises. Pre-fix
        the `RuntimeError` propagated out of the barrier untouched and the session's record
        stayed authoritative; the boundary stales first and normalizes after.
        """
        harness, session, raised = self.staled_harness(
            lambda t, rq, ob: MalformedEvidenceFieldEvidence.around(conforming(t, rq, ob))
        )
        self.assertIsInstance(raised, OrcaRuntimeError)
        self.assertTrue(str(raised).startswith(MODEL_SELECTION_UNVERIFIED))
        self.assertIn("RuntimeError", str(raised))
        self.assertIn(MalformedEvidenceFieldEvidence.MESSAGE, str(raised))
        self.assertNoRecordFor(harness, session, role="reviewer")

    def test_the_refusal_does_not_render_the_evidence_that_just_raised(self) -> None:
        """A diagnostic must not be able to replace the failure it is diagnosing.

        `_model_refusal()` renders every field of the evidence it is given, which is the
        operation that just raised. So the normalized refusal is built with `evidence=None`
        and carries the exception's own text instead -- otherwise the boundary would raise a
        second, unrelated `TypeError` from inside its own handler and lose both the
        invalidation report and the diagnosis.
        """
        _harness, _session, raised = self.staled_harness(
            lambda t, rq, ob: conforming(t, rq, ob, resolved=123)
        )
        self.assertIn("evidence=none", str(raised))
        self.assertIn("expected string or bytes-like object", str(raised))

    def test_a_refusal_leg_that_raises_while_rendering_is_still_normalized(self) -> None:
        """The second escape route out of the vocabulary, closed by the same boundary.

        A non-string `observed_at_*` field reaches a leg that DOES refuse, and then
        `_model_refusal()` raises a `TypeError` from its own `":".join(...)` while rendering
        the diagnosis. Staling had already happened (that leg went through `refuse()`), but
        the caller got a raw `TypeError` instead of the refusal. Both halves are now closed:
        the session is non-authoritative AND the failure is in the vocabulary.
        """
        harness, session, raised = self.staled_harness(
            lambda t, rq, ob: conforming(
                t, rq, ob, state="bogus_state", observed_at_task=None
            )
        )
        self.assertIsInstance(
            raised, OrcaRuntimeError,
            f"the refusal renderer's own TypeError escaped as {type(raised).__name__}",
        )
        self.assertNoRecordFor(harness, session, role="reviewer")

    def test_an_interrupt_during_validation_still_propagates_as_itself(self) -> None:
        """`BaseException` is the invalidation boundary and NOT the normalization boundary.

        The R1 distinction, applied to the validation legs as well as to the driver call: a
        `KeyboardInterrupt` arriving mid-validation leaves the session just as switched, so
        it must stale -- but it is not a failed model selection and must keep propagating as
        itself rather than becoming an `OrcaRuntimeError`.
        """
        class Interrupting(ModelEvidence):
            @property
            def requested_model(self):               # type: ignore[override]
                raise KeyboardInterrupt("operator stopped the run mid-validation")

            @classmethod
            def around(cls, base):
                import dataclasses

                obj = object.__new__(cls)
                for field in dataclasses.fields(ModelEvidence):
                    if field.name != "requested_model":
                        object.__setattr__(obj, field.name, getattr(base, field.name))
                return obj

        _recorder, harness = self.harness_for(resolving_per_session("model-a"))
        session = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_one", session, role="reviewer", phase="implementation", attempt=1
        )
        harness.model_driver = RecordingDriver(
            lambda t, rq, ob: Interrupting.around(conforming(t, rq, ob))
        )
        with self.assertRaises(KeyboardInterrupt):
            harness.verify_model_identity(
                "task_two", session, role="reviewer", phase="implementation", attempt=2
            )
        self.assertNoRecordFor(harness, session, role="reviewer")

    def test_malformed_evidence_cannot_launder_a_later_delivery(self) -> None:
        """The consequence, not just the refusal: the staled session cannot admit a pair.

        B3's cost is identical to B1's -- a record that outlives the session it describes
        granting pair admission to a later delivery. So the end state is asserted the same
        way B1's is, through a delivery on a separate legitimate session.
        """
        driver = sessions_resolving(["model-b"], ["model-a"])
        _recorder, harness = self.harness_for(driver)
        reviewer = self.session(harness, "reviewer")
        worker = self.session(harness, "worker")
        harness.verify_model_identity(
            "task_rev", reviewer, role="reviewer", phase="implementation", attempt=1
        )
        harness.verify_model_identity(
            "task_wkr", worker, role="worker", phase="implementation", attempt=1
        )
        # The REVIEWER's session is re-verified with malformed evidence.
        harness.model_driver = RecordingDriver(
            lambda t, rq, ob: conforming(t, rq, ob, resolved=123)
        )
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_model_identity(
                "task_rev2", reviewer, role="reviewer", phase="implementation", attempt=2
            )
        self.assertNoRecordFor(harness, reviewer, role="reviewer")
        # The Worker delivery must no longer be admitted on the reviewer's dead record.
        harness.model_driver = driver
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.deliver(harness, worker, "worker")
        self.assertTrue(
            str(caught.exception).startswith(MODEL_SELECTION_PAIR_UNADMITTED),
            f"expected {MODEL_SELECTION_PAIR_UNADMITTED}, got: {caught.exception}",
        )


# ---- F-001 (phase Reviewer, BUGFIX iteration 1) ----------------------------------------

class EvilStrError(Exception):
    """`__str__` raises. The shape the phase Reviewer reproduced F-001 with."""

    def __str__(self):                               # type: ignore[override]
        raise RuntimeError("evil-str")


class EvilReprError(Exception):
    """`__repr__` raises and `__str__` is fine -- the shape a `str()`-only guard survives
    but its `repr()` FALLBACK does not."""

    def __repr__(self):                              # type: ignore[override]
        raise RuntimeError("evil-repr")


class EvilBothError(Exception):
    """BOTH raise. Nothing can be rendered, so the diagnostic must say that and not raise."""

    def __str__(self):                               # type: ignore[override]
        raise RuntimeError("evil-str")

    def __repr__(self):                              # type: ignore[override]
        raise RuntimeError("evil-repr")


# OS-49 BUGFIX iteration 3 (review F-002). Timings for the asynchronous-interrupt
# regressions. The ratio is what makes them deterministic rather than racy: the render
# busy-loops for two orders of magnitude longer than the one-shot timer, so the signal is
# always delivered DURING the render, never before it starts or after it finishes.
_INTERRUPT_TIMER_SECONDS = 0.02
_RENDER_BUSY_SECONDS = 2.0


class OperatorInterruptMixin:
    """Deliver a REAL asynchronous `KeyboardInterrupt` into a running render.

    A synchronous `raise KeyboardInterrupt` inside `__str__` cannot stand in for this: the
    whole point of F-002 is that the boundary cannot tell the two apart by type, so only
    an interrupt that arrives from outside the interpreter's control flow -- the signal
    machinery, exactly as Ctrl-C arrives -- actually tests the claim.
    """

    def _require_signal_timer(self) -> None:
        """Skip, rather than fail or flake, where signal delivery is not available."""
        if not hasattr(signal, "setitimer") or not hasattr(signal, "SIGALRM"):
            self.skipTest("signal.setitimer/SIGALRM unavailable on this platform")
        if threading.current_thread() is not threading.main_thread():
            self.skipTest("Python delivers signals only on the main thread")

    @contextlib.contextmanager
    def _operator_interrupt_armed(self, exception=KeyboardInterrupt):
        """Arm a one-shot timer whose handler raises `exception`, and always disarm it.

        The `finally:` is not boilerplate. If the body propagates the interrupt (which is
        the passing case) the timer and the previous handler must still be restored, or a
        later unrelated test inherits an armed timer and fails for no visible reason.
        """
        def _deliver(signum, frame):                 # pragma: no cover - timing dependent
            raise exception("delivered asynchronously by the operator")

        previous = signal.signal(signal.SIGALRM, _deliver)
        try:
            signal.setitimer(signal.ITIMER_REAL, _INTERRUPT_TIMER_SECONDS)
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, previous)


class HostileRenderEvidence(ModelEvidence):
    """A `ModelEvidence` whose `requested_model` raises an exception that cannot be rendered.

    The construction matters, and it is what makes this regression honest rather than
    decorative. THREE constructions were tried against F-001:

      1. `HostileRenderEvidence(state=..., requested_model=...)` -- IMPOSSIBLE. The frozen
         dataclass `__init__` assigns to every field, `requested_model` is a property with
         no setter, and the subclass cannot be instantiated at all:
         `AttributeError: property 'requested_model' of 'HostileRenderEvidence' object has
         no setter`.
      2. Re-classing a normally-built `ModelEvidence` through
         `object.__setattr__(obj, "__class__", HostileRenderEvidence)` -- WORKS as a
         technique (a property is a data descriptor and wins over the instance `__dict__`,
         so the read does raise), but does NOT reproduce F-001 when applied to an instance
         the TEST built, because the driver helpers (`resolving_per_session` and friends)
         construct their OWN `conforming(...)` evidence inside `select_and_verify()`. The
         object the harness validates is never the one that was re-classed, so the hostile
         read is not on the harness's actual read path. The LOCATION, not the technique,
         is what failed.
      3. `object.__new__` plus a per-field `object.__setattr__`, wrapped around the
         evidence the DRIVER returns -- reproduces it. That is this class, and it is the
         same construction `MalformedEvidenceFieldEvidence` already uses; a driver that
         returns a malformed object is not obliged to have built it the way the dataclass
         intends.
    """

    error_type = EvilStrError

    @property
    def requested_model(self):                       # type: ignore[override]
        raise self.error_type("the text of this exception cannot be produced")

    @classmethod
    def around(cls, base: ModelEvidence) -> "HostileRenderEvidence":
        import dataclasses

        obj = object.__new__(cls)
        for field in dataclasses.fields(ModelEvidence):
            if field.name != "requested_model":
                object.__setattr__(obj, field.name, getattr(base, field.name))
        return obj


class StrRaisingEvidence(HostileRenderEvidence):
    error_type = EvilStrError


class ReprRaisingEvidence(HostileRenderEvidence):
    error_type = EvilReprError


class BothRaisingEvidence(HostileRenderEvidence):
    error_type = EvilBothError


class HostileDiagnosticRenderTests(
    OperatorInterruptMixin, PostSelectionValidationBoundaryTests
):
    """F-001. The normalization path may not be able to RAISE while diagnosing.

    Pre-fix failure mode, reproduced as an EXECUTING escape before the fix and quoted in
    the Worker Result: the post-selection handler staled the session correctly, re-raised
    `OrcaRuntimeError` and non-`Exception` control flow untouched, and then built its
    `detail=` with the EAGER f-string `f"{type(exc).__name__}: {exc}"`. For an ordinary
    `Exception` whose `__str__` raises, rendering `{exc}` raised a NEW
    `RuntimeError('evil-str')` from inside the handler, so the caller received a raw
    `RuntimeError` instead of `model_selection_unverified`. The staling half was already
    right; the vocabulary half was not -- and the handler's own comment states the
    principle it broke, that a diagnostic must not be able to replace the failure it is
    diagnosing.

    Subclasses `PostSelectionValidationBoundaryTests` deliberately: every assertion of the
    boundary this finding reopened runs again against the hardened renderer rather than
    being replaced by it.

    All three hostile shapes are covered, because a fix that guarded only `str()` would
    still escape through the obvious `repr()` fallback.
    """

    HOSTILE = (
        ("str_raises", StrRaisingEvidence, EvilStrError),
        ("repr_raises", ReprRaisingEvidence, EvilReprError),
        ("both_raise", BothRaisingEvidence, EvilBothError),
    )

    def test_every_hostile_render_shape_is_refused_in_vocabulary(self) -> None:
        """The finding itself, as a closed set: in-vocabulary refusal AND staling.

        Both halves are asserted for every shape, because F-001's observed result had the
        staling half RIGHT and the vocabulary half wrong -- a regression that checked only
        invalidation would have passed against the defect.
        """
        for label, evidence_cls, error_cls in self.HOSTILE:
            with self.subTest(shape=label):
                harness, session, raised = self.staled_harness(
                    lambda t, rq, ob, cls=evidence_cls: cls.around(conforming(t, rq, ob))
                )
                self.assertIsInstance(
                    raised, OrcaRuntimeError,
                    f"{label} escaped the closed vocabulary as {type(raised).__name__}",
                )
                self.assertTrue(
                    str(raised).startswith(MODEL_SELECTION_UNVERIFIED),
                    f"expected {MODEL_SELECTION_UNVERIFIED}, got: {raised}",
                )
                # The diagnosis still names the exception TYPE, which is the part that
                # survives a hostile renderer and the part a reader needs.
                self.assertIn(error_cls.__name__, str(raised))
                # The original cause stays attached, so nothing about the failure is lost.
                self.assertIsInstance(raised.__cause__, error_cls)
                # And the invariant: the session this selection may have switched is no
                # longer authoritative anywhere.
                self.assertNoRecordFor(harness, session, role="reviewer")

    def test_the_unrenderable_shape_says_so_rather_than_saying_nothing(self) -> None:
        """When nothing can be rendered the diagnostic reports THAT, and still refuses.

        A total renderer that silently produced an empty string would pass the vocabulary
        assertion above while telling a reader nothing, so the stand-in text is asserted
        through the production constant rather than through a literal.
        """
        _harness, _session, raised = self.staled_harness(
            lambda t, rq, ob: BothRaisingEvidence.around(conforming(t, rq, ob))
        )
        self.assertIn(UNRENDERABLE_TEXT, str(raised))

    def test_a_driver_raising_a_hostile_exception_is_normalized_too(self) -> None:
        """The SIBLING line, which would have escaped identically.

        The driver-failure handler built its own diagnostic with the same eager
        `f"{type(exc).__name__}: {exc}"`, and that render sits OUTSIDE any try/except at
        all -- the handler has already exited by the time `driver_failure` is formatted --
        so a driver raising `EvilStrError` escaped with no boundary above it whatsoever.
        The review asked for the boundary, not the one line the finding named.
        """
        class HostileDriver:
            def select_and_verify(self, ticket):
                raise EvilStrError("the driver's own failure cannot be rendered")

            def capabilities(self):                  # pragma: no cover - not reached
                return ()

        _recorder, harness = self.harness_for(resolving_per_session("model-a"))
        session = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_one", session, role="reviewer", phase="implementation", attempt=1
        )
        harness.model_driver = HostileDriver()
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.verify_model_identity(
                "task_two", session, role="reviewer", phase="implementation", attempt=2
            )
        self.assertTrue(str(caught.exception).startswith(MODEL_SELECTION_UNVERIFIED))
        self.assertIn("the model-selection driver raised", str(caught.exception))
        self.assertIn("EvilStrError", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, EvilStrError)
        self.assertNoRecordFor(harness, session, role="reviewer")

    def test_an_interrupt_from_a_hostile_renderer_propagates_as_itself(self) -> None:
        """INVERTED in iteration 3 (review F-002). It used to require the opposite.

        As written in iteration 2 this test asserted that a `KeyboardInterrupt` raised by
        a hostile `__str__` is CAUGHT and converted to `model_selection_unverified`,
        reasoning that such an interrupt "is not an operator stopping the run". That
        reasoning encoded the defect as correct: it describes the renderer's INTENT, and
        intent is exactly what this boundary cannot observe, so the `except BaseException`
        it justified also consumed a REAL asynchronous operator interrupt
        (`test_an_asynchronous_operator_interrupt_during_rendering_propagates`).

        Recorded plainly because it is the FIFTH test in this PR found to assert the
        defect rather than the requirement: a test derived from the implementation's own
        argument inherits that argument's error.

        The inverted expectation is the one the surrounding design already states. The
        post-selection handler stales the session and then re-raises non-`Exception`
        control flow untouched under the R1 rule, so a driver that raises
        `KeyboardInterrupt` from `__str__` is treated identically to one that raises it
        from `select_and_verify()` -- it propagates as itself. The staling half still
        holds, and that is asserted here too: propagation is not permission to leave stale
        evidence authoritative.
        """
        class InterruptingStr(Exception):
            def __str__(self):                       # type: ignore[override]
                raise KeyboardInterrupt("raised by the renderer")

        class Shape(HostileRenderEvidence):
            error_type = InterruptingStr

        harness, session, raised = self.staled_harness(
            lambda t, rq, ob: Shape.around(conforming(t, rq, ob))
        )
        self.assertIsInstance(
            raised, KeyboardInterrupt,
            "a renderer-raised KeyboardInterrupt was normalized into "
            f"{type(raised).__name__} instead of propagating as itself",
        )
        self.assertNoRecordFor(harness, session, role="reviewer")

    def test_an_asynchronous_operator_interrupt_during_rendering_propagates(self) -> None:
        """F-002. The operator's Ctrl-C, delivered WHILE the diagnostic renders.

        This is the shape iteration 2 missed, and it is not the synchronous one above.
        Nothing in the evidence raises `KeyboardInterrupt`: the driver fails with an
        ORDINARY `Exception`, the boundary catches it correctly and starts rendering it
        for the refusal `detail=`, and the interrupt arrives from OUTSIDE -- the signal
        machinery -- in the middle of that render. Pre-fix the blanket `except
        BaseException` in `safe_text()` consumed it and `verify_model_identity()` returned
        an ordinary refusal, so the operator's interrupt was lost and execution continued:

            SWALLOWED: got OrcaRuntimeError: model_selection_unverified: refusing to
            deliver a task before the requested model is positively verified; ...

        Determinism, deliberately, rather than a sleep race: the hostile `__str__` busy-
        loops on `time.monotonic()` for `_RENDER_BUSY_SECONDS`, which is two orders of
        magnitude longer than the `_INTERRUPT_TIMER_SECONDS` one-shot timer, so the signal
        is always delivered inside the render. A Python-level busy loop is required --
        `time.sleep()` would be interrupted by the signal rather than hosting it. If the
        timer somehow does not fire, `__str__` returns a sentinel that fails the assertion
        loudly instead of passing quietly. Skip-guarded rather than flaky on platforms
        without `setitimer` or off the main thread, where signal delivery is not
        available: a flaky interrupt test gets deleted later and the protection is lost
        with it.
        """
        self._require_signal_timer()
        started: list[bool] = []

        class SlowStrError(Exception):
            """An ORDINARY exception whose render is slow enough to host the interrupt."""

            def __str__(self):                       # type: ignore[override]
                started.append(True)
                deadline = time.monotonic() + _RENDER_BUSY_SECONDS
                while time.monotonic() < deadline:
                    pass
                return "<the interrupt timer never fired>"

        class Shape(HostileRenderEvidence):
            error_type = SlowStrError

        # `staled_harness()` is deliberately NOT reused here, and that is a correctness
        # point rather than a style one. It performs a FIRST verification before swapping
        # the driver, and arming the timer around the whole helper would let a slow
        # machine deliver the interrupt during that setup instead of during the render --
        # which still raises `KeyboardInterrupt`, so the test would PASS while having
        # tested nothing. The timer is therefore armed around the second verification
        # only, after all setup is complete.
        _recorder, harness = self.harness_for(resolving_per_session("model-a"))
        session = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_one", session, role="reviewer", phase="implementation", attempt=1
        )
        self.assertIn(session, harness._model_session_identity)
        harness.model_driver = RecordingDriver(
            lambda t, rq, ob: Shape.around(conforming(t, rq, ob))
        )

        raised: BaseException | None = None
        with self._operator_interrupt_armed():
            try:
                harness.verify_model_identity(
                    "task_two", session, role="reviewer", phase="implementation",
                    attempt=2,
                )
            except BaseException as exc:             # noqa: BLE001 - that is the subject
                raised = exc
            else:                                    # pragma: no cover - a failure
                self.fail("the hostile evidence was ACCEPTED")

        # Belt and braces on the timing: if the interrupt had landed outside the render,
        # `__str__` would never have run and this test would be vacuous. Asserted rather
        # than assumed, so a mis-timed delivery fails loudly instead of passing.
        self.assertTrue(
            started,
            "the hostile __str__ never ran, so the interrupt was not delivered during "
            "rendering and this test proved nothing",
        )
        self.assertIsInstance(
            raised, KeyboardInterrupt,
            "an asynchronous operator interrupt delivered during diagnostic rendering "
            f"was swallowed and replaced by {type(raised).__name__}: "
            f"{str(raised)[:160]}",
        )
        self.assertNoRecordFor(harness, session, role="reviewer")

    def test_a_systemexit_during_rendering_propagates(self) -> None:
        """The same class of escape as F-002, pinned separately because it is a separate
        type. `SystemExit` is finality, not a rendering failure; a diagnostic that eats it
        turns a requested shutdown into a refusal and the process keeps running.
        """
        class ExitingStr(Exception):
            def __str__(self):                       # type: ignore[override]
                raise SystemExit(3)

        class Shape(HostileRenderEvidence):
            error_type = ExitingStr

        harness, session, raised = self.staled_harness(
            lambda t, rq, ob: Shape.around(conforming(t, rq, ob))
        )
        self.assertIsInstance(
            raised, SystemExit,
            f"SystemExit raised during rendering was consumed as {type(raised).__name__}",
        )
        self.assertEqual(raised.code, 3)
        self.assertNoRecordFor(harness, session, role="reviewer")


class TotalRendererUnitTests(OperatorInterruptMixin, unittest.TestCase):
    """The renderer itself, directly -- including the legs no barrier test can reach.

    `safe_text` is now load-bearing at every `except` handler in the harness that renders
    what it caught, so it is tested as the primitive it is and not only through the one
    boundary the finding named.
    """

    def test_a_benign_exception_renders_byte_identically_to_the_eager_form(self) -> None:
        """The compatibility half. No existing message, log row or test expectation moves.

        The fix is only allowed to change behaviour where the eager f-string RAISED, so
        the equality is asserted against that exact f-string rather than against literals.
        """
        for exc in (
            ValueError("plain"),
            TypeError("expected string or bytes-like object, got 'int'"),
            OrcaRuntimeError("model_selection_unverified: ..."),
            RuntimeError(""),
            KeyError("missing"),
        ):
            with self.subTest(exception=type(exc).__name__):
                self.assertEqual(safe_text(exc), str(exc))
                self.assertEqual(safe_type_name(exc), type(exc).__name__)
                self.assertEqual(
                    safe_exception_text(exc), f"{type(exc).__name__}: {exc}"
                )

    def test_each_hostile_leg_falls_back_one_step_and_never_raises(self) -> None:
        """`str` -> `repr` -> stand-in, one leg at a time."""
        # `__repr__` hostile, `__str__` fine: the first leg answers, so nothing moves.
        self.assertEqual(safe_text(EvilReprError("shown by str")), "shown by str")
        # `__str__` hostile: the `repr()` leg answers and SAYS that it is the fallback.
        rendered = safe_text(EvilStrError("hidden"))
        self.assertIn("repr: ", rendered)
        self.assertIn("EvilStrError", rendered)
        # Both hostile: the fixed stand-in, and still no raise.
        self.assertEqual(safe_text(EvilBothError("hidden")), UNRENDERABLE_TEXT)
        self.assertEqual(
            safe_exception_text(EvilBothError("hidden")),
            f"EvilBothError: {UNRENDERABLE_TEXT}",
        )

    def test_a_non_string_or_raising_type_name_is_also_total(self) -> None:
        """`type(value).__name__` is not guaranteed readable, or even a string.

        Unreachable from the barrier -- a metaclass this hostile is not something a driver
        is likely to hand back -- but the renderer promises TOTAL, and a promise with an
        untested leg is an assumption.
        """
        class RaisingName(type):
            @property
            def __name__(cls):                       # type: ignore[override]
                raise RuntimeError("no name for you")

        class NonStringName(type):
            __name__ = 7                             # type: ignore[assignment]

        class A(Exception, metaclass=RaisingName):
            pass

        class B(Exception, metaclass=NonStringName):
            pass

        self.assertEqual(safe_type_name(A()), UNRENDERABLE_TYPE_NAME)
        self.assertEqual(safe_type_name(B()), UNRENDERABLE_TYPE_NAME)
        # And the composed form still refuses to raise.
        self.assertIn(UNRENDERABLE_TYPE_NAME, safe_exception_text(A()))

    def test_the_logging_guard_still_swallows_a_hostile_writer_failure(self) -> None:
        """`_safe_log`'s promise is unconditional, so its own render must be total.

        Section 9 says a logging failure never changes a lifecycle decision. An eager
        `f"...{error}"` inside the guard could break that promise from inside the guard
        itself -- F-001 with a different consequence: an already-settled Dispatch turning
        into an apparent failure.
        """
        harness = OrcaRuntimeHarness.__new__(OrcaRuntimeHarness)
        harness._logging_errors = []

        def hostile_writer():
            raise EvilBothError("unrenderable logging failure")

        harness._safe_log(hostile_writer)            # must not raise
        self.assertEqual(len(harness._logging_errors), 1)
        self.assertIn("hostile_writer", harness._logging_errors[0])
        self.assertIn(UNRENDERABLE_TEXT, harness._logging_errors[0])

    # ---- the eager-render boundary guard (F-001 structurally; rebuilt for F-003) -------

    #: The `except ... as NAME` identifiers the harness actually uses. Asserted as a
    #: REQUIRED SUBSET below so this guard cannot pass vacuously: if the traversal stops
    #: matching handlers, these go missing and the test fails loudly instead of scanning
    #: nothing and reporting success. A subset rather than equality so that adding a
    #: handler named something else is not a spurious failure -- vacuity is the failure
    #: mode being defended against, not novelty.
    EXPECTED_HANDLER_NAMES = frozenset({"exc", "error", "refusal"})

    #: Floor on the number of named handlers, same anti-vacuity purpose. 16 at the time of
    #: writing; a floor because handlers get added, and a drift that silently scans fewer
    #: is exactly what F-003 found.
    EXPECTED_HANDLER_FLOOR = 16

    #: The caught-variable ALIASES that escape their handler, as (handler, alias). This is
    #: the focused structural assertion F-003 asked for: `driver_failure` is the ninth
    #: site, it is assigned from `exc` inside the handler and rendered AFTER the handler
    #: has exited, and the old line-based scanner could not see it by construction.
    EXPECTED_ESCAPING_ALIASES = frozenset({("exc", "driver_failure")})

    @staticmethod
    def _eager_renders_of(scope, target: str) -> list[int]:
        """Lines inside `scope` that render the name `target` EAGERLY.

        The three shapes the fix replaced, matched on the parse tree rather than on text:
        an f-string interpolation of the bare name, `str(name)`/`repr(name)`, and
        `type(name).__name__`. Matching on the tree is what lets this see through line
        breaks and, more importantly, distinguishes `f"{exc}"` (an eager render, the
        defect) from `f"{safe_exception_text(exc)}"` (a Call, the fix) without a regex
        that has to guess.
        """
        hits = []
        for node in ast.walk(scope):
            if (
                isinstance(node, ast.FormattedValue)
                and isinstance(node.value, ast.Name)
                and node.value.id == target
            ):
                hits.append(node.lineno)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("str", "repr")
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == target
            ):
                hits.append(node.lineno)
            elif (
                isinstance(node, ast.Attribute)
                and node.attr == "__name__"
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "type"
                and len(node.value.args) == 1
                and isinstance(node.value.args[0], ast.Name)
                and node.value.args[0].id == target
            ):
                hits.append(node.lineno)
        return sorted(hits)

    def test_no_eager_caught_exception_render_is_left_in_the_harness(self) -> None:
        """The BOUNDARY, asserted structurally rather than one site at a time.

        F-001 was the second time this pattern was fixed at one site while a sibling line
        kept it alive, so this regression is written against the SOURCE: no
        `except ... as NAME` block in the harness may render NAME eagerly, and no alias of
        such a name may be rendered eagerly after the handler exits either.

        Rebuilt on `ast` in iteration 3 (review F-003). The previous version walked lines
        and stopped at the first line dedented to the `except` indentation, which made two
        failures structural rather than accidental:

          1. It could not see the shape it claimed to cover. A caught exception saved into
             an alias and rendered LATER is outside the handler suite, so the scan had
             already stopped. Demonstrated by the call-sites-only revert, where this guard
             reported eight reverted sites and missed the ninth -- `driver_failure` --
             while a separate direct test was what actually caught it.
          2. It could pass vacuously. Nothing asserted that any handler had been scanned,
             so if the regex ever stopped matching, `offenders == []` would still hold and
             the guard would report success while checking nothing.

        Both are fixed here: aliases assigned from a caught name are tracked and then
        scanned across the WHOLE enclosing function, and the traversal's own coverage is
        asserted before its result is trusted.
        """
        module = ast.parse(
            (REPO_ROOT / "scripts" / "orca_runtime_harness.py").read_text(encoding="utf-8")
        )
        parents = {}
        for parent in ast.walk(module):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        def enclosing_scope(node):
            """The function a handler lives in -- the region an escaped alias is live in."""
            cursor = parents.get(node)
            while cursor is not None:
                if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return cursor
                cursor = parents.get(cursor)
            return module

        scanned_names, scanned_handlers, aliases, offenders = set(), 0, set(), []
        for node in ast.walk(module):
            if not isinstance(node, ast.ExceptHandler) or not node.name:
                continue
            scanned_names.add(node.name)
            scanned_handlers += 1
            for line in self._eager_renders_of(node, node.name):
                offenders.append(f"{line}: eager render of caught `{node.name}`")
            # Aliases that ESCAPE: `driver_failure = exc` keeps the caught object alive
            # past the handler, so the render that matters is outside the suite entirely.
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Assign)
                    and len(sub.targets) == 1
                    and isinstance(sub.targets[0], ast.Name)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == node.name
                ):
                    alias = sub.targets[0].id
                    aliases.add((node.name, alias))
                    for line in self._eager_renders_of(enclosing_scope(node), alias):
                        offenders.append(
                            f"{line}: eager render of `{alias}`, an alias of caught "
                            f"`{node.name}` that outlives its handler"
                        )

        # ---- anti-vacuity: trust the result only if the traversal actually traversed ----
        self.assertGreaterEqual(
            scanned_handlers, self.EXPECTED_HANDLER_FLOOR,
            f"the handler traversal found only {scanned_handlers} named `except` handlers "
            f"(expected at least {self.EXPECTED_HANDLER_FLOOR}); this guard reports "
            "success when it scans nothing, so under-scanning is itself the failure",
        )
        self.assertLessEqual(
            self.EXPECTED_HANDLER_NAMES, scanned_names,
            "expected handler names were not scanned: missing "
            f"{sorted(self.EXPECTED_HANDLER_NAMES - scanned_names)}",
        )
        self.assertLessEqual(
            self.EXPECTED_ESCAPING_ALIASES, aliases,
            "the caught-variable alias that outlives its handler was not tracked: "
            f"missing {sorted(self.EXPECTED_ESCAPING_ALIASES - aliases)}. That alias is "
            "the F-003 site; if it is gone, re-point this assertion deliberately rather "
            "than deleting it",
        )

        self.assertEqual(
            offenders, [],
            "a caught exception (or an alias of one) is rendered eagerly; route it "
            "through safe_text()/safe_exception_text() -- see review F-001/F-003:\n"
            + "\n".join(offenders),
        )

    # ---- F-002, at the renderer itself rather than through the boundary ----------------

    def test_safe_text_lets_an_asynchronous_operator_interrupt_through(self) -> None:
        """F-002 at the primitive. `safe_text` is total for `Exception`, not for Ctrl-C.

        The boundary-level regression proves the operator keeps control of a real run;
        this one pins the single line responsible, so a future re-widening to
        `BaseException` fails here even if the boundary test is restructured.
        """
        self._require_signal_timer()

        started: list[bool] = []

        class SlowStr:
            def __str__(self):
                started.append(True)
                deadline = time.monotonic() + _RENDER_BUSY_SECONDS
                while time.monotonic() < deadline:
                    pass
                return "<the interrupt timer never fired>"

        with self.assertRaises(KeyboardInterrupt):
            with self._operator_interrupt_armed():
                safe_text(SlowStr())
        self.assertTrue(started, "the interrupt did not land inside the render")

    def test_safe_text_lets_an_asynchronous_systemexit_through(self) -> None:
        """Same line, the other control-flow type. `SystemExit` is not a render failure."""
        self._require_signal_timer()

        started: list[bool] = []

        class SlowStr:
            def __str__(self):
                started.append(True)
                deadline = time.monotonic() + _RENDER_BUSY_SECONDS
                while time.monotonic() < deadline:
                    pass
                return "<the interrupt timer never fired>"

        with self.assertRaises(SystemExit):
            with self._operator_interrupt_armed(SystemExit):
                safe_text(SlowStr())
        self.assertTrue(started, "the interrupt did not land inside the render")

    def test_the_renderer_stays_total_for_every_ordinary_exception_type(self) -> None:
        """The other half of the F-002 narrowing: `Exception` must still be absorbed.

        Narrowing the catch is only correct if it did not re-open F-001, so the realistic
        hostile-render failures are asserted as a SET rather than trusting the class
        hierarchy argument in the docstring. `MemoryError` and `RecursionError` are in it
        because the Reviewer named them specifically: both are `Exception` subclasses and
        both must still be caught.
        """
        for label, raised in (
            ("RuntimeError", RuntimeError("boom")),
            ("MemoryError", MemoryError()),
            ("RecursionError", RecursionError()),
            ("TypeError", TypeError("boom")),
            ("ValueError", ValueError("boom")),
            ("AttributeError", AttributeError("boom")),
        ):
            with self.subTest(raised=label):
                class Hostile:
                    def __str__(self, _raised=raised):
                        raise _raised

                    def __repr__(self, _raised=raised):
                        raise _raised

                self.assertEqual(safe_text(Hostile()), UNRENDERABLE_TEXT)

    def test_the_control_flow_types_are_outside_exception_on_this_interpreter(self) -> None:
        """The narrowing's premise, asserted rather than assumed.

        The whole fix rests on where CPython draws the `Exception`/`BaseException` line. If
        that ever moved, `except Exception` would silently start or stop covering a type
        and the two sets above would quietly change meaning, so the premise is pinned.
        """
        for cls in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(propagates=cls.__name__):
                self.assertFalse(issubclass(cls, Exception))
        for cls in (MemoryError, RecursionError, RuntimeError):
            with self.subTest(absorbed=cls.__name__):
                self.assertTrue(issubclass(cls, Exception))



# ---- N1 --------------------------------------------------------------------------------

class ModelAwareGuardFailClosedTests(ModelSessionTestCase):
    """N1. The model-aware predicate must not read "cannot answer" as "no model declared".

    Pre-fix failure mode: `getattr(routing, "is_model_aware", False)` inside a fail-closed
    barrier. On the real `RunRouting` it is a property and always answers, so no genuine
    routing was affected -- the exposure is a duck-typed, partially constructed or otherwise
    malformed routing object, for which `False` meant Gate B's (role, phase, attempt)
    argument check SKIPPED ITSELF silently on a run that may well declare models. A guard
    that disappears when it cannot evaluate itself is not a guard.
    """

    class NoAttribute:
        """A routing with entries but no `is_model_aware` at all."""

        def for_role(self, phase, role):
            return None

    class RaisingAttribute:
        """A routing whose `is_model_aware` raises -- the same unanswered question."""

        @property
        def is_model_aware(self):
            raise RuntimeError("the routing could not decide")

        def for_role(self, phase, role):
            return None

    def test_a_routing_that_cannot_answer_is_treated_as_model_aware(self) -> None:
        for label, routing in (
            ("missing", self.NoAttribute()),
            ("raising", self.RaisingAttribute()),
        ):
            with self.subTest(routing=label):
                self.assertTrue(
                    OrcaRuntimeHarness._routing_is_model_aware(routing),
                    f"a {label} is_model_aware was read as 'this run declares no model', "
                    "which skips the barrier's fail-closed argument check",
                )

    def test_none_is_still_the_legacy_answer(self) -> None:
        """`None` is the explicit legacy sentinel, not a malformed object: it answers False.

        The whole no-routing path depends on it, and reading it as model-aware would refuse
        every pre-OS-49 dispatch.
        """
        self.assertFalse(OrcaRuntimeHarness._routing_is_model_aware(None))

    def test_a_real_routing_still_answers_for_itself(self) -> None:
        both = routing_from(SPLIT_PROFILE, "split")
        self.assertTrue(OrcaRuntimeHarness._routing_is_model_aware(both))
        self.assertEqual(
            OrcaRuntimeHarness._routing_is_model_aware(both), both.is_model_aware
        )

    def test_the_barrier_refuses_rather_than_skipping_on_a_malformed_routing(self) -> None:
        """The consequence at the site that matters: an omitted identity is a REFUSAL.

        Pre-fix the barrier returned silently and the dispatch proceeded unverified; the
        argument check now fires, which is observable.
        """
        _recorder, harness = self.harness_for(resolving_per_session("model-a"))
        harness.agent_routing = self.NoAttribute()
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness._verify_model_identity(
                task_id="task_g", terminal="term_x", role="", phase="", attempt=0,
                require_pair_admission=True,
            )
        self.assertIn("this run's routing declares a model", str(caught.exception))

    def test_no_fail_open_getattr_default_is_left_in_the_guard(self) -> None:
        """Read off the source: the `False` default must not come back by copy-paste.

        There were two sites and both were the same one-line shape, so the thing worth
        locking is the SHAPE's absence, not either site.
        """
        import inspect

        # The two GUARD SITES, not the whole class: `_routing_is_model_aware()`'s own
        # docstring quotes the old shape in order to explain why it was wrong, and a test
        # that forbade the string everywhere would forbid documenting the fix.
        for guard in (
            OrcaRuntimeHarness._verify_model_identity,
            OrcaRuntimeHarness._log_agent_identity_row,
        ):
            source = inspect.getsource(guard)
            self.assertNotIn(
                'getattr(routing, "is_model_aware"', source,
                f"{guard.__name__}() reads is_model_aware directly again; a fail-open "
                "default is back in a safety-critical guard",
            )
            self.assertIn("_routing_is_model_aware(routing)", source)


# ---- N2 / N3: documentation that must match behaviour ----------------------------------

class DocumentationAccuracyTests(unittest.TestCase):
    """N2 and N3. Two documentation defects, locked so they cannot silently return.

    These are the two findings with no behavioural component, and a prose fix with no test
    is a fix that regresses on the next edit. Both are asserted against the real sources.
    """

    def test_the_routing_key_docstring_does_not_claim_reachability_only(self) -> None:
        """N2. `_routing_key()` changed what `resolved_agent_command()` can RETURN.

        The iteration-2 docstring said the Final Review mapping "widens the slot's
        REACHABILITY only", which was wrong by omission: the public accessors built on this
        mapping now return the Final Reviewer slot's command and model for a Final Review
        attempt where they used to return "".
        """
        import inspect

        source = inspect.getsource(OrcaRuntimeHarness._routing_key)
        # The removed SENTENCE, not a quotation of it: the corrected docstring quotes the
        # old claim in order to say it was wrong, which is the point of the correction.
        self.assertNotIn("This widens the slot's REACHABILITY only.", source)
        self.assertIn("resolved_agent_command", source)
        self.assertIn("behaviour change in the public accessors", source)

    def test_resolved_agent_command_does_not_claim_unchanged_behaviour(self) -> None:
        """N2, the second half: the accessor's OWN docstring made the same claim."""
        import inspect

        source = inspect.getsource(OrcaRuntimeHarness.resolved_agent_command)
        self.assertNotIn("Behaviour UNCHANGED by OS-49.", source)
        self.assertIn("final_review", source)

    def test_the_compatibility_doc_spells_the_refusal_code_correctly(self) -> None:
        """N3. The doc named a constant that does not exist.

        Asserted against `agent_profile`'s own constant rather than against the string, so
        a future rename moves both together instead of leaving the doc behind again.
        """
        from scripts.agent_profile import REASON_ROLE_UNRESOLVED

        text = (REPO_ROOT / "docs" / "COMPATIBILITY.md").read_text(encoding="utf-8")
        self.assertNotIn(
            "AGENT_PROFILE_ROLE_UNRESOLVED", text,
            "the compatibility doc names a refusal code that does not exist",
        )
        self.assertIn(REASON_ROLE_UNRESOLVED, text)


if __name__ == "__main__":
    unittest.main()
