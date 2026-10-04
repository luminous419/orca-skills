#!/usr/bin/env python3
"""OS-49 FINAL BUGFIX round: two PR #37 review comments' regressions, plus meta-tests.

SCOPE. Iteration 5 (review R20) replaced a blanket property claim here, which had stopped
being true. Three populations of tests live here, added at three different times for three
different reasons:

  1. REVIEW COMMENT 5954727286 -- findings B1, N1, N2, extended in iteration 2 by the two
     blocking findings R1 and R2 of the mandatory Final Adversarial Review, by the B3
     post-selection validation boundary, and by F-001's hostile-renderer work. These are
     the classes from `StaleSessionEvidenceTests` down to `DocumentationAccuracyTests`.
  2. REVIEW COMMENT 5970292670 -- findings N-1a, N-1b, N-2, N-3, N-4, the post-settlement
     model-provenance round, in the banner-marked block that begins at `RaisingBool`. A
     SEPARATE finding family that reuses the same letters for different defects; its own
     banner says so, and the letters are NOT cross-referenced between the two reviews.
  3. The ITERATION-4 META-TESTS, which lock the claim-shape discipline rather than
     reproducing a production defect: `TotalityClaimAccuracyTests`,
     `RendererHelperBehaviourTests` and `ClassDocstringClaimShapeTests`. Their read
     subjects differ, so iteration 6 (review F-005) names them one at a time, and
     `Population3ReadSubjectFactTests` (iteration 7, review F-006) compares three kinds
     of claim in this docstring against a LABEL SET it derives per top-level class.
     That class's docstring says what the comparison does and does not establish.
     `TotalityClaimAccuracyTests` reads PRODUCTION harness source -- `inspect.getsource`
     over the eight `INVENTORY` subjects in `scripts/orca_runtime_harness.py` -- plus
     its own class's source. Re-inserting a banned unconditional claim into the
     production docstring of `safe_exception_text`, one of those eight sites, fails
     `test_no_unconditional_totality_claim_survives` at that location: the eight-site
     lock working as designed.
     `RendererHelperBehaviourTests` reads no source text at all; it calls `safe_text` and
     `safe_repr` and asserts what they return and what they propagate.
     `ClassDocstringClaimShapeTests` reads THIS file via `Path(__file__).read_text()`. So
     does `Population3ReadSubjectFactTests`.

Where a class docstring here names the finding it locks and the pre-fix failure mode, that
is why: a regression that passes before and after proves nothing. That is a drafting
convention, not a checked invariant.
`test_no_unsupported_class_universal_survives_the_module_docstring` is the assertion that
fails this file for re-quantifying the convention over classes while the derived census
still holds a counterexample -- `StrRaisingEvidence`, `ReprRaisingEvidence` and
`BothRaisingEvidence` are bare evidence fixtures carrying no docstring at all, and
`TotalRendererUnitTests` documents the renderer primitive rather than a finding.

WHAT WAS DEMONSTRATED. Iteration 5 deleted a blanket claim here that an older baseline
fails this file: an isolated `e5dead8` tree overlaid with this file collects ZERO tests and
stops during collection, because that baseline cannot import
`MODEL_SELECTION_OBSERVATION_METHODS`, and a run that collects nothing demonstrates no
failed assertion at all. What was demonstrated is per fix, not per file, and the record
this points at covers REVIEW COMMENT 5970292670's round: the N-1a..N-4 fixes were reverted
INDIVIDUALLY from the fixed tree -- six named reverts, all collecting 89 tests -- and the
regressions belonging to each reverted fix failed, with the collection count reported
before every result so that a `SyntaxError`/`ImportError` executing no assertion could not
be miscounted as a kill. Those six reverts and their counts are in
`artifacts/runs/run_abb0c1bbffae/BUGFIX.md` under "Revert experiment", and that file's
per-iteration validation sections hold the sibling mutations for the eight-site lock.
Review comment 5954727286's own earlier round is recorded in its own run artifacts.
Population 3 is locked by mutation instead of by revert -- a banned claim shape re-inserted
into a class docstring, and in iteration 5 into THIS docstring -- reported the same way,
collection count first.

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
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
import unittest
from pathlib import Path

from scripts import run_logging
from scripts.agent_profile import (
    EVENT_AGENT_IDENTITY_BOUND,
    REASON_WORKER_REVIEWER_MUST_DIFFER,
)
from scripts.deterministic_workflow.fake_adapter import InProcessModelDriver
from scripts.agent_profile import MODEL_EVIDENCE_VERIFIED
from unittest.mock import patch
from scripts.orca_runtime_harness import (
    MODEL_SELECTION_AMBIGUOUS,
    MODEL_SELECTION_OBSERVATION_METHODS,
    MODEL_SELECTION_PAIR_UNADMITTED,
    MODEL_SELECTION_UNSUPPORTED,
    MODEL_SELECTION_UNVERIFIED,
    UNRENDERABLE_REPR,
    UNRENDERABLE_TEXT,
    UNRENDERABLE_TYPE_NAME,
    ModelEvidence,
    OrcaRuntimeError,
    OrcaRuntimeHarness,
    safe_exception_text,
    safe_repr,
    safe_text,
    safe_type_name,
)
from scripts.test_orca_runtime_contract import SequentialTerminalExec
from scripts.test_os49_bugfix_regressions import (
    _verified_resolver,
    resolving_per_session,
)
from scripts.test_orca_runtime_contract import RecordingExec
from scripts.test_os49_delivery_barrier import (
    BarrierTestCase,
    DISTINCT_COMMAND_PROFILE,
    RecordingDriver,
    SPLIT_PROFILE,
    conforming,
    routing_from,
)
from scripts.test_os49_model_provenance import ProvenanceTestCase, log_rows

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

    What "recovery" means here, stated precisely (review 5970292670, N-3): re-establishing
    AUTHORITY for a model the run has already accepted for that role. It is not a way to
    move a role onto a different model. Whether the re-verification happens on the spent
    session or on a brand-new one, leg (i) still compares against
    `_model_role_history[(phase, role)]`, so the resolved model must equal the role's
    established baseline. Changing a role's model requires a new run. Both halves are
    asserted in this class, the second by
    `test_a_fresh_session_must_still_resolve_to_the_roles_baseline`.
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

        Qualified (review 5970292670, N-3), because "a NEW session" on its own reads as
        though any fresh session recovers a drifted ROLE, and it does not. The fresh
        session clears leg (k), which is keyed on the terminal; leg (i) is keyed on
        (phase, routing role) and is untouched by moving sessions, so within this run the
        replacement must still resolve to the ROLE'S ESTABLISHED BASELINE. A replacement
        resolving to a different model for that role is refused on the new session too --
        locked by `test_a_fresh_session_must_still_resolve_to_the_roles_baseline`. A
        different resolved model for a role is a new RUN's business.

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

    Three of the four tests here drive the REAL `start_run()` twice with NO `finish()`
    between, which is the review's stated reproduction shape;
    `test_the_reset_is_at_the_boundary_and_not_only_on_the_clean_exit` drives no run at
    all and reads the source of both boundaries instead. The distribution is spelled out
    because the aggregate form of this sentence was false of that fourth method -- found
    by `ClassDocstringClaimShapeTests` in OS-49 BUGFIX iteration 4, not by a reader, which
    is the point of adding that check (review F-004).
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


class ResolvedModelReadRaisingEvidence(ModelEvidence):
    """A `ModelEvidence` whose `resolved_model` RAISES when it is READ.

    OS-49 BUGFIX (final review F-013). `test_the_refusal_does_not_render_the_evidence_
    that_just_raised` used to drive `resolved_model=123`, whose `TypeError` came out of
    the regex leg. The F-013 type gate now REFUSES that shape by name before any leg
    touches it, so `123` no longer reaches the B3 normalization path at all and could no
    longer demonstrate the invariant that path exists for. The invariant is unchanged and
    is not weakened: it is re-driven here through a field whose READ raises, which no type
    check can pre-empt, and which `_model_refusal()` would re-enter if it were handed this
    evidence -- `resolved_model` is one of the fields that refusal builder renders.

    Built through `object.__new__` for the reason `MalformedEvidenceFieldEvidence` states:
    the frozen dataclass `__init__` cannot assign through a property.
    """

    MESSAGE = "reading resolved_model is itself the failure"

    @property
    def resolved_model(self):                        # type: ignore[override]
        raise RuntimeError(self.MESSAGE)

    @classmethod
    def around(cls, base: ModelEvidence) -> "ResolvedModelReadRaisingEvidence":
        import dataclasses

        obj = object.__new__(cls)
        for field in dataclasses.fields(ModelEvidence):
            if field.name != "resolved_model":
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
        second, unrelated exception from inside its own handler and lose both the
        invalidation report and the diagnosis.

        OS-49 BUGFIX (final review F-013). The INPUT moved and the assertions did not. The
        superseded driver returned `resolved_model=123`, which reached this path because
        the regex leg raised a `TypeError` on it; the F-013 type gate now refuses that
        shape by name, which is a better outcome and is locked by
        `AttestedFieldTypeGateTests`, but it means `123` no longer exercises the B3
        normalization this test is about. `ResolvedModelReadRaisingEvidence` raises from
        the field READ instead -- the one failure mode no type check can pre-empt, on a
        field `_model_refusal()` renders -- so the `evidence=none` assertion below is the
        same assertion about the same boundary.
        """
        _harness, _session, raised = self.staled_harness(
            lambda t, rq, ob: ResolvedModelReadRaisingEvidence.around(
                conforming(t, rq, ob)
            )
        )
        self.assertIn("evidence=none", str(raised))
        self.assertIn("RuntimeError", str(raised))
        self.assertIn(ResolvedModelReadRaisingEvidence.MESSAGE, str(raised))

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

    def test_each_hostile_leg_falls_back_one_step_for_an_ordinary_exception(self) -> None:
        """`str` -> `repr` -> stand-in, one leg at a time, for an ordinary `Exception`.

        Scope, stated because the superseded method name said "never raises" and that
        has not been true since review F-002 narrowed the captures in `safe_text` from
        `BaseException` to `Exception` (the same N-4 wording defect this file locks at
        the production sites). Every specimen below raises an ordinary `Exception` from
        `__str__` / `__repr__`, which is the half of the split `safe_text` absorbs.
        `KeyboardInterrupt`, `SystemExit` and `GeneratorExit` PROPAGATE instead, which
        `test_safe_text_lets_an_asynchronous_operator_interrupt_through` and
        `test_safe_text_lets_an_asynchronous_systemexit_through` assert directly. The
        name is qualified rather than the guard widened: widening it to `BaseException`
        would reintroduce F-002.
        """
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

    def test_a_non_string_or_exception_raising_type_name_is_also_handled(self) -> None:
        """`type(value).__name__` is not guaranteed readable, or even a string.

        Unreachable from the barrier -- a metaclass this hostile is not something a driver
        is likely to hand back -- but the renderer promises totality FOR ORDINARY
        `Exception`, and a promise with an untested leg is an assumption.

        Scope, stated because the superseded method name said "is also total" with no
        qualifier: the raising specimen below raises `RuntimeError`, an ordinary
        `Exception`, and the other returns a non-string without raising at all.
        `safe_type_name` catches `Exception`, not `BaseException`, so a `__name__` that
        raises `KeyboardInterrupt`, `SystemExit` or `GeneratorExit` PROPAGATES as itself.
        That is the deliberate N-4 split, not an untested leg, and it is not to be closed
        by re-widening the capture.
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
        """`_safe_log` absorbs ordinary `Exception`, so its own render must be total there.

        Section 9 says a logging failure never changes a lifecycle decision. An eager
        `f"...{error}"` inside the guard could break that promise from inside the guard
        itself -- F-001 with a different consequence: an already-settled Dispatch turning
        into an apparent failure.

        Scope, stated because the superseded first line called the promise
        "unconditional" and the render "total" with no qualifier: the hostile writer below
        raises `EvilBothError`, an ordinary `Exception`. `_safe_log` catches `Exception`,
        not `BaseException`, so a writer raising `KeyboardInterrupt`, `SystemExit` or
        `GeneratorExit` PROPAGATES and records nothing --
        `test_an_interrupt_from_the_identity_row_still_propagates` asserts that at the
        boundary. The method name is left alone because a hostile writer failure IS an
        ordinary failure, which is what it says.
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


# ========================================================================================
# Review 5970292670 (at 9334b15): N-1a, N-1b, N-2, N-3, N-4.
#
# A SEPARATE finding family from the B1/B2/B3 and N1/N2/N3 above, which used the same
# letters for different defects. These five are the post-settlement model-provenance
# safety round and every one of them was reproduced against 9334b15 before being fixed.
# ========================================================================================


class RaisingBool:
    """Truthiness raises; everything else renders. The review's literal N-1 shape.

    It is the hostile object that matters most, because `_log_agent_identity_row()`'s
    `detail` assembly evaluates `(evidence.observation_method ...) or 'none'` -- so
    truthiness, not rendering, is what the settled-dispatch logging funnel touches first.
    """

    def __bool__(self):
        raise RuntimeError("hostile __bool__")

    def __str__(self):
        return "in_process_session_state"

    def __repr__(self):
        return "RaisingBool()"


class RaisingRender:
    """Truthy, but both renderings raise -- the other half of "raising string-rendering".

    Separate from `RaisingBool` deliberately: a fix that guarded only truth-value
    evaluation would let this one through, and a fix that guarded only rendering would let
    `RaisingBool` through. Both are asserted.
    """

    def __bool__(self):
        return True

    def __str__(self):
        raise RuntimeError("hostile __str__")

    def __repr__(self):
        raise RuntimeError("hostile __repr__")


class HostileEquality(str):
    """A `str` SUBCLASS whose `__eq__` raises.

    Why the contract is `type(...) is str` and not `isinstance(...)`: an `isinstance`
    gate admits this object, and the very next statement is a membership test over
    `MODEL_SELECTION_OBSERVATION_METHODS`, which compares with `==`. The defect would
    simply move one line down.
    """

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")

    def __hash__(self):
        return 0


# ---- N-1a: `observation_method` is validated, not merely rendered ----------------------

class ObservationMethodContractTests(ModelSessionTestCase):
    """N-1a. The one attested `ModelEvidence` field the barrier never looked at.

    Pre-fix, `observation_method` was declared `str`, validated nowhere, and read at
    exactly two sites that RENDER it. So an arbitrary object passed the barrier, was
    stored as authority by the accept block, and was evaluated later -- from the
    settled-dispatch logging funnel, after the dispatch it authorized had completed.

    These tests assert the barrier itself: malformed observation metadata is NOT ACCEPTED
    FOR DELIVERY. The separate question of what happens when the logging path raises
    anyway is `SettledDispatchLoggingBoundaryTests`, below, and it is deliberately NOT
    made unreachable by this fix.
    """

    ADMIT_PAIR = True

    #: Every shape the review names, plus the two closed-set cases. The first two are the
    #: reproductions; the rest are the contract the fix states.
    MALFORMED = (
        ("raising __bool__", RaisingBool()),
        ("raising __str__/__repr__", RaisingRender()),
        ("str subclass with raising __eq__", HostileEquality("in_process_session_state")),
        ("None", None),
        ("int", 7),
        ("empty string", ""),
        ("unknown locator", "claude_slash_model"),
    )

    def attempt_with(self, observation_method, *, harness=None, recorder=None,
                     terminal=None, task_id="task_obs", attempt=1):
        """Drive the DELIVERY barrier with one malformed `observation_method`."""
        driver = RecordingDriver(
            lambda t, rq, ob: conforming(
                t, rq, ob, observation_method=observation_method
            )
        )
        if harness is None:
            recorder, harness = self.harness_for(InProcessModelDriver())
            terminal = self.session(harness, "worker")
        harness.model_driver = driver
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness._verify_model_identity(
                task_id=task_id, terminal=terminal, role="worker",
                phase="implementation", attempt=attempt,
                require_pair_admission=False,
            )
        return str(caught.exception), harness, recorder

    def test_every_malformed_observation_method_is_refused_before_delivery(self) -> None:
        """The barrier refuses, nothing is delivered, and the reason is SPECIFIC.

        `model_selection_unsupported` is asserted rather than merely "some refusal":
        pre-fix a hostile `__repr__` could reach `_model_refusal()` and replace the real
        reason with the B3 boundary's generic malformed-evidence normalization, which is
        exactly what the review's fix point 3 forbids.
        """
        for label, value in self.MALFORMED:
            with self.subTest(observation_method=label):
                recorder, harness = self.harness_for(InProcessModelDriver())
                terminal = self.session(harness, "worker")
                message, harness, recorder = self.attempt_with(
                    value, harness=harness, recorder=recorder, terminal=terminal
                )
                self.assertTrue(
                    message.startswith(MODEL_SELECTION_UNSUPPORTED),
                    f"expected {MODEL_SELECTION_UNSUPPORTED}, got: {message}",
                )
                self.assertIn("observation_method", message)
                self.assertNothingDelivered(recorder)
                # And no authority was created by the refused attempt.
                self.assertNoRecordFor(harness, terminal, role="worker")

    def test_a_hostile_repr_cannot_replace_the_refusal_it_is_diagnosing(self) -> None:
        """Fix point 3, asserted on the message itself.

        The diagnostic still names the field; it just renders the unrenderable value as a
        stand-in instead of raising out of the refusal builder.
        """
        message, _harness, _recorder = self.attempt_with(RaisingRender())
        self.assertTrue(message.startswith(MODEL_SELECTION_UNSUPPORTED), message)
        self.assertIn(UNRENDERABLE_REPR, message)
        self.assertIn("not str", message)

    def test_the_only_supported_locator_is_still_accepted(self) -> None:
        """The positive control. A fix that refused everything would also pass the above."""
        recorder, harness = self.harness_for(InProcessModelDriver())
        terminal = self.session(harness, "worker")
        harness._verify_model_identity(
            task_id="task_ok", terminal=terminal, role="worker",
            phase="implementation", attempt=1, require_pair_admission=False,
        )
        record = harness._model_identity[("implementation", "worker")]
        self.assertEqual(
            record.observation_method, MODEL_SELECTION_OBSERVATION_METHODS[0]
        )
        self.assertEqual(record.state, MODEL_EVIDENCE_VERIFIED)

    def test_the_value_is_refused_rather_than_coerced_into_a_valid_one(self) -> None:
        """No `str()` / `safe_text()` rescue: the stand-in is never written as authority.

        `RaisingBool.__str__` returns the SUPPORTED locator verbatim, so a fix that
        normalized the field before testing it would accept this object and record a
        perfectly well-formed-looking row. That is the acceptance the review forbids by
        name, and it is what this test exists to catch.
        """
        recorder, harness = self.harness_for(InProcessModelDriver())
        terminal = self.session(harness, "worker")
        self.assertEqual(str(RaisingBool()), MODEL_SELECTION_OBSERVATION_METHODS[0])
        message, harness, _recorder = self.attempt_with(
            RaisingBool(), harness=harness, recorder=recorder, terminal=terminal
        )
        self.assertTrue(message.startswith(MODEL_SELECTION_UNSUPPORTED), message)
        self.assertNoRecordFor(harness, terminal, role="worker")
        self.assertEqual(harness._model_identity, {})

    def test_the_rejection_stales_authority_but_retains_non_drift_history(self) -> None:
        """The review's exact wording, as two separate assertions on two separate maps.

        Sequence: accept `model-a` on S (authority + history written), then a second
        attempt on S whose `observation_method` is malformed. The refusal is
        POST-selection -- the driver already ran and may have switched S -- so authority
        for S must be gone. History must NOT be, and the proof that it survived is
        behavioural rather than introspective: a third attempt resolving to `model-b` is
        refused as DRIFT, which is only possible if the baseline still exists.
        """
        recorder, harness = self.harness_for(resolving_per_session("model-a"))
        terminal = self.session(harness, "worker")
        harness._verify_model_identity(
            task_id="task_1", terminal=terminal, role="worker",
            phase="implementation", attempt=1, require_pair_admission=False,
        )
        self.assertEqual(
            harness._model_role_history[("implementation", "worker")], "model-a"
        )

        message, harness, _recorder = self.attempt_with(
            RaisingBool(), harness=harness, recorder=recorder, terminal=terminal,
            task_id="task_2", attempt=2,
        )
        self.assertTrue(message.startswith(MODEL_SELECTION_UNSUPPORTED), message)
        # Authority: GONE, on all four of the places that record it.
        self.assertNoRecordFor(harness, terminal, role="worker")
        # History: RETAINED -- both maps, read directly...
        self.assertEqual(
            harness._model_role_history[("implementation", "worker")], "model-a"
        )
        self.assertEqual(
            harness._model_session_history[terminal][2], "model-a"
        )
        # ...and asserted behaviourally, which is what B2 actually protects: the drift
        # baseline survived the refusal, so a later drift is still refused rather than
        # sliding through on a cleared slate.
        harness.model_driver = resolving_per_session("model-b")
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness._verify_model_identity(
                task_id="task_3", terminal=terminal, role="worker",
                phase="implementation", attempt=3, require_pair_admission=False,
            )
        self.assertTrue(
            str(caught.exception).startswith(MODEL_SELECTION_AMBIGUOUS),
            str(caught.exception),
        )
        self.assertIn("model-a", str(caught.exception))

    def test_control_flow_exceptions_still_propagate_through_the_new_leg(self) -> None:
        """The N-1 fix must not have widened anything. A driver raising KeyboardInterrupt
        from the evidence it returns still leaves as itself, and still stales."""

        class InterruptingRender:
            def __bool__(self):
                raise KeyboardInterrupt

        recorder, harness = self.harness_for(InProcessModelDriver())
        terminal = self.session(harness, "worker")
        harness.model_driver = RecordingDriver(
            lambda t, rq, ob: conforming(
                t, rq, ob, observation_method=InterruptingRender()
            )
        )
        # The TYPE test comes first and refuses without evaluating truthiness, so this
        # object is refused rather than interrupting -- which is the point: validation
        # does not touch the hostile object at all.
        with self.assertRaises(OrcaRuntimeError):
            harness._verify_model_identity(
                task_id="task_i", terminal=terminal, role="worker",
                phase="implementation", attempt=1, require_pair_admission=False,
            )

        # And a driver that raises control flow DIRECTLY is unchanged by this round.
        def interrupting(_ticket):
            raise KeyboardInterrupt

        harness.model_driver = type(
            "D", (), {"select_and_verify": staticmethod(interrupting)}
        )()
        with self.assertRaises(KeyboardInterrupt):
            harness._verify_model_identity(
                task_id="task_i2", terminal=terminal, role="worker",
                phase="implementation", attempt=2, require_pair_admission=False,
            )
        self.assertNoRecordFor(harness, terminal, role="worker")


# ---- F-012: the VALIDATED value is the value STORED as authority -----------------------

class HostileObservationValue:
    """The F-012 payload: a value whose truth test and whose renderings raise.

    Deliberately the same hostile shape N-1a's `RaisingRender` uses, with truthiness
    raising as well, so a stored instance of it detonates at the first thing any reader
    does with it -- `_log_agent_identity_row()`'s `or 'none'` evaluates truthiness and the
    refusal diagnostics render.
    """

    def __bool__(self):
        raise RuntimeError("hostile __bool__")

    def __str__(self):
        raise RuntimeError("hostile __str__")

    def __repr__(self):
        raise RuntimeError("hostile __repr__")


class TimeVaryingObservationEvidence(ModelEvidence):
    """A `ModelEvidence` subclass whose `observation_method` answers honestly, then lies.

    `HONEST_READS` is the number of reads `_verify_model_identity()` made of this field
    BEFORE the F-012 fix -- the exact-`str` check and the closed-set membership check --
    so the pre-fix barrier sees the supported locator at both of its validating reads and
    ACCEPTS this evidence. That is what makes this a lock rather than a tautology: an
    object refused at the door would prove nothing about what the accept block stores.
    Every read after those answers with `HostileObservationValue()`.

    Built through `object.__new__` plus per-field `object.__setattr__`, the construction
    `MalformedEvidenceFieldEvidence` and `HostileRenderEvidence` already use: the frozen
    dataclass `__init__` cannot assign through a property, and `frozen=True` blocks
    `__setattr__` only -- it does nothing about a subclass redeclaring a field, which is
    the whole reason F-012 existed.
    """

    HONEST_READS = 2

    @property
    def observation_method(self):                    # type: ignore[override]
        reads = object.__getattribute__(self, "_reads")
        reads.append(True)
        if len(reads) <= type(self).HONEST_READS:
            return MODEL_SELECTION_OBSERVATION_METHODS[0]
        return HostileObservationValue()

    @property
    def reads(self) -> int:
        """How many times `observation_method` has been read off THIS object."""
        return len(object.__getattribute__(self, "_reads"))

    @classmethod
    def around(cls, base: ModelEvidence) -> "TimeVaryingObservationEvidence":
        import dataclasses

        obj = object.__new__(cls)
        for field in dataclasses.fields(ModelEvidence):
            if field.name != "observation_method":
                object.__setattr__(obj, field.name, getattr(base, field.name))
        object.__setattr__(obj, "_reads", [])
        return obj


class GetattributeVaryingEvidence(ModelEvidence):
    """The same lie told through `__getattribute__` instead of through a property.

    Held as a SECOND specimen because this is the shape the Final Adversarial Review and
    the Coordinator each probed with, and it is the one `frozen=True` is most obviously
    silent about: `frozen` installs a raising `__setattr__` and touches attribute READS
    not at all. A fix that somehow bound redeclared properties only would still be open
    here, so the lock is asserted over both shapes rather than over the convenient one.

    `HONEST_READS` has the same meaning as on `TimeVaryingObservationEvidence` and the
    same consequence: the pre-fix barrier's two validating reads both see the supported
    locator, so this evidence is ACCEPTED pre-fix.
    """

    HONEST_READS = 2

    def __getattribute__(self, name):
        if name != "observation_method":
            return object.__getattribute__(self, name)
        reads = object.__getattribute__(self, "_reads")
        reads.append(True)
        if len(reads) <= object.__getattribute__(self, "HONEST_READS"):
            return MODEL_SELECTION_OBSERVATION_METHODS[0]
        return HostileObservationValue()

    @property
    def reads(self) -> int:
        """How many times `observation_method` has been read off THIS object."""
        return len(object.__getattribute__(self, "_reads"))

    @classmethod
    def around(cls, base: ModelEvidence) -> "GetattributeVaryingEvidence":
        import dataclasses

        obj = object.__new__(cls)
        for field in dataclasses.fields(ModelEvidence):
            object.__setattr__(obj, field.name, getattr(base, field.name))
        object.__setattr__(obj, "_reads", [])
        return obj


class StashingDriver:
    """A driver that records the evidence OBJECT it handed back, for identity assertions."""

    def __init__(self, evidence) -> None:
        self.evidence = evidence
        self.returned: list[ModelEvidence] = []

    def select_and_verify(self, ticket):
        request_stamp = ticket.stamp()
        observe_stamp = ticket.stamp()
        result = self.evidence(ticket, request_stamp, observe_stamp)
        self.returned.append(result)
        return result


class CanonicalEvidenceAuthorityTests(ModelSessionTestCase):
    """F-012. The value a leg VALIDATED must be the value the accept block STORES.

    Pre-fix failure mode, reproduced by the Final Adversarial Review and independently by
    the Coordinator: `ModelEvidence` is `@dataclass(frozen=True)`, but `frozen` blocks
    `__setattr__` only and says nothing about a SUBCLASS that redeclares a field as a
    property. Admission is `isinstance(evidence, ModelEvidence)`, so a subclass is
    accepted; N-1a's two checks then read `observation_method` twice and the accept block
    stored the DRIVER'S OWN OBJECT into `_model_identity`, `_model_pending_evidence` and
    `_model_session_identity`. A subclass answering the supported locator for exactly
    those two reads was therefore accepted while the authority maps held an object whose
    NEXT read returned anything it liked -- a time-of-check/time-of-use split that put an
    arbitrary value back on the path N-1a exists to close, after the dispatch it
    authorized had settled.

    The fix is canonicalization, NOT exact-class rejection at admission. Admission stays
    `isinstance` on purpose: production constructs the exact class only, and the
    `ModelEvidence` subclasses in this repository are adversarial fixtures whose purpose
    is to reach LATER legs -- `MalformedEvidenceFieldEvidence`, `HostileRenderEvidence`
    and the `Interrupting` shape in `PostSelectionValidationBoundaryTests`. Rejecting
    subclasses at the door would re-route those fixtures instead of fixing anything.
    `test_a_time_varying_observation_method_cannot_poison_any_authority_map` is the
    assertion that fires on the stored value, and it is the §14 lock this finding
    requires. It drives `VARYING_SHAPES`, so the redeclared-property specimen and the
    `__getattribute__` specimen -- the shape both probes used -- are each exercised.
    """

    #: The two ways a subclass can make `observation_method` answer differently on
    #: different reads. Both are driven, because the fix must not depend on which
    #: attribute-access hook the driver chose.
    VARYING_SHAPES = (
        ("redeclared property", TimeVaryingObservationEvidence),
        ("__getattribute__ override", GetattributeVaryingEvidence),
    )

    def accept_time_varying(self, shape=TimeVaryingObservationEvidence):
        """Accept one time-varying evidence shape at the barrier.

        Returns (harness, terminal, driver). Acceptance is asserted by the absence of a
        refusal: the pre-fix barrier accepts this evidence too, so acceptance is common
        ground and the assertions that discriminate are about what was STORED.
        """
        _recorder, harness = self.harness_for(InProcessModelDriver())
        terminal = self.session(harness, "worker")
        driver = StashingDriver(
            lambda t, rq, ob: shape.around(conforming(t, rq, ob))
        )
        harness.model_driver = driver
        harness._verify_model_identity(
            task_id="task_f012", terminal=terminal, role="worker",
            phase="implementation", attempt=1, require_pair_admission=False,
        )
        return harness, terminal, driver

    def stored_records(self, harness, terminal):
        """The three authority maps, as (label, stored `ModelEvidence`) pairs."""
        return (
            ("_model_identity", harness._model_identity[("implementation", "worker")]),
            ("_model_pending_evidence", harness._model_pending_evidence[terminal]),
            (
                "_model_session_identity",
                harness._model_session_identity[terminal][2],
            ),
        )

    def test_a_time_varying_observation_method_cannot_poison_any_authority_map(
        self,
    ) -> None:
        """The finding itself, asserted on all three maps rather than on the first one.

        Pre-fix, the record in every one of them WAS the driver's object, so reading
        `observation_method` back returned `HostileObservationValue()` and
        `type(...) is str` fails. Post-fix the maps hold the canonical snapshot and the
        read returns the exact validated locator.
        """
        for shape_label, shape in self.VARYING_SHAPES:
            harness, terminal, driver = self.accept_time_varying(shape)
            for label, record in self.stored_records(harness, terminal):
                with self.subTest(shape=shape_label, authority=label):
                    method = record.observation_method
                    self.assertIs(
                        type(method), str,
                        f"{label} holds observation_method of type "
                        f"{safe_type_name(method)}, not str",
                    )
                    self.assertEqual(method, MODEL_SELECTION_OBSERVATION_METHODS[0])
                    # And it is actually usable: truthiness and rendering are what the
                    # settled-dispatch logging funnel and a later refusal diagnostic do.
                    self.assertTrue(bool(method))
                    self.assertIn(
                        MODEL_SELECTION_OBSERVATION_METHODS[0], f"{method!r}"
                    )

            # Non-vacuity: the driver's object still turns hostile once its honest
            # budget is spent, so the assertions above passed because the AUTHORITY
            # changed and not because the fixture stopped lying. The budget has to be
            # drained explicitly here: post-fix the barrier spends only ONE of the two
            # honest reads, which is itself the point, so the second is still owed.
            with self.subTest(shape=shape_label, authority="driver object"):
                returned = driver.returned[0]
                while returned.reads < shape.HONEST_READS:
                    returned.observation_method
                hostile = returned.observation_method
                self.assertIsNot(type(hostile), str)
                with self.assertRaises(RuntimeError):
                    bool(hostile)

    def test_the_authority_is_a_harness_owned_exact_class_record(self) -> None:
        """The structural half: the driver does not own the object the maps hold.

        Asserted separately from the value because a fix that merely re-read and
        re-checked the field before storing would satisfy the value assertion and still
        leave a driver-owned object as authority for every later reader.

        OS-49 BUGFIX (final review F-013). The superseded sentence said this structural
        fact is "what makes the value durable", and that was the overclaim F-013 named: an
        exact-class wrapper says nothing about the fifteen objects stored INSIDE it, and
        the review demonstrated a driver-owned `selection_token` surviving by identity
        inside exactly such a wrapper. What this method establishes is that the CONTAINER
        is harness-built, exact-class and shared by the three maps. What makes the
        contained VALUES safe is the type gate, locked by `AttestedFieldTypeGateTests`.
        Neither half substitutes for the other.
        """
        for shape_label, shape in self.VARYING_SHAPES:
            harness, terminal, driver = self.accept_time_varying(shape)
            returned = driver.returned[0]
            records = self.stored_records(harness, terminal)
            for label, record in records:
                with self.subTest(shape=shape_label, authority=label):
                    self.assertIs(
                        type(record), ModelEvidence,
                        f"{label} holds a {type(record).__name__}, not an exact "
                        "ModelEvidence",
                    )
                    self.assertIsNot(
                        record, returned,
                        f"{label} holds the object the DRIVER returned",
                    )
            # One canonical record, shared by the three maps, so a later reader cannot
            # find two different answers to the same question.
            with self.subTest(shape=shape_label, authority="shared"):
                self.assertIs(records[0][1], records[1][1])
                self.assertIs(records[0][1], records[2][1])

    def test_the_barrier_reads_observation_method_exactly_once(self) -> None:
        """READ ONCE is the invariant, and it is what makes re-reading impossible.

        Pre-fix this counter stood at 2 immediately after acceptance -- one read per
        validating leg -- and nothing stopped a third, fourth or hundredth read by
        logging, reuse or provenance code against the stored object. Post-fix the single
        snapshot read is the only read the barrier makes, so there is no later read to
        vary.

        OS-49 BUGFIX (final review F-013), test-contract half. RENAMED. This method was
        called `test_the_barrier_reads_each_attested_field_exactly_once`, and the two
        fixtures it drives instrument `observation_method` and nothing else, so the name
        claimed fourteen fields its assertion never observed. The name now says what the
        counter counts. The fifteen-field measurement the old name promised exists, under
        that promise, as
        `AttestedFieldTypeGateTests.test_the_barrier_reads_every_attested_field_exactly_once`.
        """
        for shape_label, shape in self.VARYING_SHAPES:
            with self.subTest(shape=shape_label):
                _harness, _terminal, driver = self.accept_time_varying(shape)
                self.assertEqual(
                    driver.returned[0].reads, 1,
                    "the barrier read observation_method off the driver's object more "
                    "than once",
                )

    def test_canonicalization_alters_no_attested_value(self) -> None:
        """The compatibility half, on a CONFORMING driver: nothing is lost or rewritten.

        The canonical record must be field-for-field equal to what the driver attested,
        or the fix would be silently changing provenance rather than protecting it. Also
        the positive control for the identity assertion above: an honest driver's object
        is equally not the authority.
        """
        _recorder, harness = self.harness_for(InProcessModelDriver())
        terminal = self.session(harness, "worker")
        driver = StashingDriver(lambda t, rq, ob: conforming(t, rq, ob))
        harness.model_driver = driver
        harness._verify_model_identity(
            task_id="task_canon", terminal=terminal, role="worker",
            phase="implementation", attempt=1, require_pair_admission=False,
        )
        returned = driver.returned[0]
        record = harness._model_identity[("implementation", "worker")]
        self.assertIsNot(record, returned)
        self.assertEqual(record, returned)
        self.assertEqual(record.request_evidence, returned.request_evidence)
        # The derived records written beside the authority carry the same resolved value.
        self.assertEqual(
            harness._model_role_history[("implementation", "worker")],
            returned.resolved_model,
        )
        self.assertEqual(
            harness._model_session_history[terminal],
            ("worker", "implementation", returned.resolved_model),
        )
        self.assertEqual(
            harness._terminals[terminal]["resolved_model"], returned.resolved_model
        )
        self.assertEqual(
            harness._terminals[terminal]["model_state"], MODEL_EVIDENCE_VERIFIED
        )


# ---- F-013: the attested values, not merely the attested container --------------------

#: The sentinel an F-013 fixture raises with.  Asserted ABSENT from a type-gate refusal:
#: its presence means a leg entered the value before its type was proven, which is the
#: ORDERING half of the finding.
DRIVER_RE_ENTRY = "driver-owned value re-entered"


class CooperativeDriverValue:
    """A driver-owned object that ANSWERS every validating operation the legs perform.

    This is the F-013 reproduction specimen, generalized from the review's
    `selection_token` probe to any field. Truthiness, `==`, `!=`, `in` and tuple equality
    are the ONLY operations the barrier's legs performed on fourteen of the fifteen
    attested values before the type gate, and an object is free to define all of them
    cooperatively -- so this value passes those legs and, pre-fix, was stored by reference
    into the canonical record as authority.

    `__format__`, `__str__` and `__repr__` raise, which is what makes the acceptance
    observable rather than theoretical: the review's reproduction detonated inside
    `_rebind_model_evidence()` through `ModelEvidence.request_evidence`, which formats
    `selection_token`, leaving a half-written terminal row after the dispatch had settled.
    """

    def __bool__(self):
        return True

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    def __hash__(self):
        return 0

    def __format__(self, spec):
        raise RuntimeError(DRIVER_RE_ENTRY)

    def __str__(self):
        raise RuntimeError(DRIVER_RE_ENTRY)

    def __repr__(self):
        raise RuntimeError(DRIVER_RE_ENTRY)


class InertDriverValue:
    """A driver-owned object that RAISES from every operation a leg could perform.

    The ORDERING specimen. A type check placed at each field's point of USE would still
    run after the legs above it had touched other values, and the coordinator's audit
    named the concrete case: the request-presence leg takes the TRUTH of
    `selection_token`, and the state leg takes MEMBERSHIP of `state`, both before the one
    type check that existed. This value makes any such touch observable -- it raises
    `DRIVER_RE_ENTRY` from `__bool__`, `__eq__`, `__ne__`, `__hash__`, `__format__`,
    `__str__` and `__repr__` -- so a refusal that does NOT carry that sentinel is positive
    evidence that the gate ran first and that nothing entered the value.
    """

    def __bool__(self):
        raise RuntimeError(DRIVER_RE_ENTRY)

    def __eq__(self, other):
        raise RuntimeError(DRIVER_RE_ENTRY)

    def __ne__(self, other):
        raise RuntimeError(DRIVER_RE_ENTRY)

    def __hash__(self):
        raise RuntimeError(DRIVER_RE_ENTRY)

    def __format__(self, spec):
        raise RuntimeError(DRIVER_RE_ENTRY)

    def __str__(self):
        raise RuntimeError(DRIVER_RE_ENTRY)

    def __repr__(self):
        raise RuntimeError(DRIVER_RE_ENTRY)


class AttestedStringSubclass(str):
    """A `str` SUBCLASS carrying the honest value -- equal to it, and not exactly `str`.

    Why the gate is `type(x) is str` and not `isinstance`. This specimen compares equal to
    the value it wraps, so it satisfies every membership and equality leg, and a subclass
    is free to redeclare `__eq__`, `__format__` or `__repr__` at any later read. Nothing
    about it is malformed; what it is not is harness-usable, which is the property the
    barrier needs from a value it stores as authority.
    """


class AllFieldReadCountingEvidence(ModelEvidence):
    """Counts reads of EVERY attested field and answers honestly on each one.

    The fifteen-field counterpart of `GetattributeVaryingEvidence`, which instruments one
    field. It tells no lies at all, deliberately: its subject is the number of reads, so
    an honest answer keeps the barrier on its accept path and makes the count the only
    thing under test.

    `_counts` and the `counts` property are not `ModelEvidence` fields, so they pass
    straight through to `object.__getattribute__` uncounted.
    """

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        import dataclasses

        return tuple(field.name for field in dataclasses.fields(ModelEvidence))

    def __getattribute__(self, name):
        if name in type(self).field_names():
            counts = object.__getattribute__(self, "_counts")
            counts[name] = counts.get(name, 0) + 1
        return object.__getattribute__(self, name)

    @property
    def counts(self) -> dict:
        """Field name -> how many times it has been read off THIS object."""
        return dict(object.__getattribute__(self, "_counts"))

    @classmethod
    def around(cls, base: ModelEvidence) -> "AllFieldReadCountingEvidence":
        import dataclasses

        obj = object.__new__(cls)
        for field in dataclasses.fields(ModelEvidence):
            object.__setattr__(obj, field.name, getattr(base, field.name))
        object.__setattr__(obj, "_counts", {})
        return obj


class AttestedFieldTypeGateTests(ModelSessionTestCase):
    """F-013. A harness-owned CONTAINER does not make the values inside it harness-usable.

    Pre-fix failure mode, demonstrated by the Final Adversarial Review and reproduced
    independently by the Coordinator. F-012 made the accept block store a freshly
    constructed exact-class `ModelEvidence` built from locals read once off the driver --
    and stopped there. `ModelEvidence` is a plain frozen dataclass with no field
    validation, so those locals are still whatever OBJECTS the driver put in the fields,
    and of the fifteen attested values exactly ONE was exact-type-checked:
    `observation_method`, by N-1a. The other fourteen were validated only by operations
    the value itself defines -- truthiness, `==`, `!=`, `in`, tuple equality -- so a
    driver-owned object answering those cooperatively passed the legs and was copied by
    reference into the canonical record. All three authority maps then held executable
    driver code: the review's probe put such an object in `selection_token`, saw it
    accepted, and had `_rebind_model_evidence()` re-enter its `__format__` through
    `ModelEvidence.request_evidence` and raise, leaving `resolved_model`, `model_state`
    and `model_request_method` written and `model_request_evidence` empty -- a partially
    updated terminal row, after the dispatch that evidence authorized had settled.

    There was an ORDERING half as well, which the coordinator's audit named: the
    request-presence leg takes the TRUTH of `selection_token` and the state leg takes
    MEMBERSHIP of `state`, both ahead of the single type check, so adding checks at each
    field's point of use would have left a hostile `__bool__` or `__eq__` firing first.
    The fix is therefore a GATE, hoisted to run immediately after the attested snapshot
    and before any other leg.

    What the methods here lock, one subject each:
    `test_the_field_table_covers_every_declared_attested_field` is a drift guard over the
    dataclass declaration and passes against the pre-fix tree, which is stated plainly in
    its own docstring. `test_a_cooperative_driver_owned_value_is_refused_in_every_field`
    is the finding's acceptance half. `test_the_type_gate_runs_before_any_leg_touches_the_value`
    is its ordering half. `test_a_string_subclass_is_not_an_attested_string` is why the
    test is `type(x) is str`. `test_a_bool_is_not_an_attested_integer` is why it is
    `type(x) is int`. `test_the_f013_reproduction_cannot_reach_a_later_authority_reader`
    drives the review's own end-to-end consequence.
    `test_the_accepted_authority_holds_only_exact_primitives` and
    `test_the_barrier_reads_every_attested_field_exactly_once` are positive controls over
    a conforming driver.
    """

    ADMIT_PAIR = True

    #: Field name -> the exact type the gate requires, in `ModelEvidence` declaration
    #: order. Twelve `str`, three `int`. `int` and not "integral": `type(x) is int`
    #: rejects `bool`, which is intended and is the rule DESIGN M-14 already applies to
    #: `attempt`.
    ATTESTED_FIELD_TYPES = (
        ("state", str),
        ("requested_model", str),
        ("resolved_model", str),
        ("selection_token", str),
        ("request_method", str),
        ("request_stamp", int),
        ("observation_method", str),
        ("observe_stamp", int),
        ("capability", str),
        ("observed_at_run", str),
        ("observed_at_task", str),
        ("observed_at_terminal", str),
        ("observed_at_role", str),
        ("observed_at_phase", str),
        ("observed_at_attempt", int),
    )

    #: The refusal member a failed type gate carries, where it is not the default. Both
    #: entries are reason codes an EXISTING test already locks for a malformed value of
    #: that field, and the gate inherits them rather than renaming anything:
    #: `observation_method` is N-1a's `model_selection_unsupported`, and the twelve
    #: remaining string fields plus the three integer fields take
    #: `model_selection_unverified` -- which `resolved_model`'s own B3 malformed-type
    #: contract already requires.
    FIELD_REASONS = {"observation_method": MODEL_SELECTION_UNSUPPORTED}

    #: The attested field `_model_refusal()` does NOT render, derived by reading what that
    #: builder emits: it reports the model the ROUTING requested, which is the harness's
    #: own string, and never the driver's `requested_model` cell. So the safe-renderer
    #: stand-in assertion below is scoped to the other fourteen -- there is no render of
    #: this one to be safe about, and asserting a stand-in for it would be asserting a
    #: render that correctly does not happen.
    UNRENDERED_BY_THE_REFUSAL = ("requested_model",)

    def reason_for(self, field_name: str) -> str:
        return self.FIELD_REASONS.get(field_name, MODEL_SELECTION_UNVERIFIED)

    def attempt_with_field(self, field_name, value, *, build=None):
        """Drive the DELIVERY barrier once with ONE attested field replaced.

        Returns (harness, terminal, recorder, message). `build` lets a caller derive the
        replacement from the conforming value -- the string-subclass and bool shapes need
        the honest value in hand, and only the driver knows it.
        """
        import dataclasses

        def evidence(ticket, request_stamp, observe_stamp):
            base = conforming(ticket, request_stamp, observe_stamp)
            replacement = (
                build(getattr(base, field_name)) if build is not None else value
            )
            return dataclasses.replace(base, **{field_name: replacement})

        recorder, harness = self.harness_for(InProcessModelDriver())
        terminal = self.session(harness, "worker")
        harness.model_driver = RecordingDriver(evidence)
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness._verify_model_identity(
                task_id="task_f013", terminal=terminal, role="worker",
                phase="implementation", attempt=1, require_pair_admission=False,
            )
        return harness, terminal, recorder, str(caught.exception)

    def assertGateRefusal(self, harness, terminal, recorder, message, field_name,
                          field_type, type_name):
        """One type-gate refusal, asserted on the reason, the diagnosis and the authority.

        `type_name` is the rendered name of the REJECTED value's class, which is what the
        diagnostic reports: the value itself is hostile by definition, so the gate's own
        fragment renders `safe_type_name(...)` of it and never the value. What this helper
        asserts is that one fragment, the reason code, nothing delivered, no record for the
        terminal/role, the three authority maps empty and both histories empty -- it makes
        no claim about the driver's call record or the selection counter.
        """
        expected = self.reason_for(field_name)
        self.assertTrue(
            message.startswith(expected),
            f"{field_name}: expected {expected}, got: {message}",
        )
        self.assertIn(
            f"{field_name} is {type_name}, not {field_type.__name__}", message,
            f"{field_name}: the refusal does not name the field and its required type",
        )
        self.assertNothingDelivered(recorder)
        self.assertNoRecordFor(harness, terminal, role="worker")
        self.assertEqual(harness._model_identity, {})
        self.assertEqual(harness._model_pending_evidence, {})
        self.assertEqual(harness._model_session_identity, {})
        # The history half too: a refused attempt earns no drift baseline either.
        self.assertEqual(harness._model_role_history, {})
        self.assertEqual(harness._model_session_history, {})

    def test_the_field_table_covers_every_declared_attested_field(self) -> None:
        """The drift guard, and it is NOT a §14 lock -- it passes against the pre-fix tree.

        Its subject is a future sixteenth field. The table above and the production gate
        are two hand-maintained lists over one dataclass declaration, so this derives the
        declaration from `dataclasses.fields(ModelEvidence)` and fails if the table stops
        matching it -- at which point the per-field methods below stop covering whatever
        was added. Recorded as passing in both arms of the revert experiment rather than
        presented as a reproduction.
        """
        import dataclasses

        declared = tuple(
            (field.name, field.type) for field in dataclasses.fields(ModelEvidence)
        )
        self.assertEqual(len(declared), 15, "ModelEvidence no longer has 15 fields")
        self.assertEqual(
            tuple(name for name, _ in declared),
            tuple(name for name, _ in self.ATTESTED_FIELD_TYPES),
            "the table is not the dataclass's field list, in declaration order",
        )
        for (name, annotation), (_name, expected) in zip(
            declared, self.ATTESTED_FIELD_TYPES
        ):
            with self.subTest(field=name):
                # `from __future__ import annotations` is in force in the production
                # module, so the annotation arrives as the STRING "str" / "int".
                self.assertEqual(
                    annotation if isinstance(annotation, str) else annotation.__name__,
                    expected.__name__,
                )

    def test_a_cooperative_driver_owned_value_is_refused_in_every_field(self) -> None:
        """The finding itself, over all fifteen fields rather than over the one.

        Pre-fix, a value that answers truthiness, equality and membership cooperatively
        passed the legs for most of these fields and was stored by reference in all three
        authority maps; for the remainder it escaped as a normalized `TypeError` instead
        of a named refusal. Post-fix each one is refused by name, before any leg, and
        nothing delivers or records the evidence as authority or history. The refusal is
        post-selection, so the ticket and both ordinals are spent and the driver's own
        call record stands -- see `assertGateRefusal` for what is asserted.
        """
        for field_name, field_type in self.ATTESTED_FIELD_TYPES:
            with self.subTest(field=field_name):
                harness, terminal, recorder, message = self.attempt_with_field(
                    field_name, CooperativeDriverValue()
                )
                self.assertGateRefusal(
                    harness, terminal, recorder, message, field_name, field_type,
                    "CooperativeDriverValue",
                )

    def test_the_type_gate_runs_before_any_leg_touches_the_value(self) -> None:
        """The ORDERING half: the gate is the FIRST thing to see the value.

        `InertDriverValue` raises `DRIVER_RE_ENTRY` from every operation a leg could
        perform, so the sentinel's ABSENCE from the refusal is positive evidence that no
        truthiness, comparison, membership, formatting or rendering of the value happened
        before its type was proven. Pre-fix the state leg's membership test and the
        request-presence leg's truthiness test each fired first and the sentinel came back
        inside a normalized `RuntimeError`.

        The refusal still renders the REJECTED driver object, which is deliberate and is
        what fix point 3 requires: it goes through `safe_repr` / `safe_text`, so the
        stand-in text appears instead of the diagnostic replacing the refusal. That
        stand-in is asserted for the fourteen fields `_model_refusal()` actually renders;
        `UNRENDERED_BY_THE_REFUSAL` says which one it does not and why.
        """
        for field_name, field_type in self.ATTESTED_FIELD_TYPES:
            with self.subTest(field=field_name):
                harness, terminal, recorder, message = self.attempt_with_field(
                    field_name, InertDriverValue()
                )
                self.assertGateRefusal(
                    harness, terminal, recorder, message, field_name, field_type,
                    "InertDriverValue",
                )
                self.assertNotIn(
                    DRIVER_RE_ENTRY, message,
                    f"{field_name}: a leg entered the value before its type was proven",
                )
                self.assertNotIn(
                    "RuntimeError", message,
                    f"{field_name}: the refusal is a normalized exception, not the gate",
                )
                if field_name not in self.UNRENDERED_BY_THE_REFUSAL:
                    self.assertTrue(
                        UNRENDERABLE_REPR in message or UNRENDERABLE_TEXT in message,
                        f"{field_name}: the diagnostic did not use the safe renderers",
                    )

    def test_a_string_subclass_is_not_an_attested_string(self) -> None:
        """`type(x) is str`, not `isinstance`, asserted on the twelve string fields.

        The subclass carries the honest value, so it satisfies every membership and
        equality leg and pre-fix was ACCEPTED and stored as authority in eleven of the
        twelve -- `observation_method` being the one field that already had the check.
        A subclass may redeclare `__eq__`, `__format__` or `__repr__` at any later read,
        which is why equality to a valid value is not the property the barrier needs.
        """
        for field_name, field_type in self.ATTESTED_FIELD_TYPES:
            if field_type is not str:
                continue
            with self.subTest(field=field_name):
                harness, terminal, recorder, message = self.attempt_with_field(
                    field_name, None, build=AttestedStringSubclass
                )
                self.assertGateRefusal(
                    harness, terminal, recorder, message, field_name, field_type,
                    "AttestedStringSubclass",
                )

    def test_a_bool_is_not_an_attested_integer(self) -> None:
        """`type(x) is int` rejects `bool`, on the three integer fields.

        DESIGN M-14's rule, applied to the ordinals and the attempt number: `True == 1`,
        so a bool attestation satisfies an arithmetic leg while being a different kind of
        thing, and M-14 is the measured record of what that aliasing costs. The injected
        value is `True` against an honest ordinal of 1 or an honest attempt of 1, so it is
        numerically indistinguishable and is refused anyway -- which is the point.
        """
        for field_name, field_type in self.ATTESTED_FIELD_TYPES:
            if field_type is not int:
                continue
            with self.subTest(field=field_name):
                harness, terminal, recorder, message = self.attempt_with_field(
                    field_name, True
                )
                self.assertGateRefusal(
                    harness, terminal, recorder, message, field_name, field_type,
                    "bool",
                )

    def test_the_f013_reproduction_cannot_reach_a_later_authority_reader(self) -> None:
        """The review's own end-to-end consequence, asserted at the later reader.

        Pre-fix: a `CooperativeDriverValue` in `selection_token` was accepted, so
        `_model_pending_evidence` held it, and `_rebind_model_evidence()` -- the first
        later reader, which runs when the Dispatch id finally exists -- evaluated
        `evidence.request_evidence`, re-entered the value's `__format__` and raised, having
        already written three of the five model cells. Post-fix the barrier refuses, the
        pending map is empty, the rebind is a no-op and the row carries the no-evidence
        shape rather than a partial one.
        """
        harness, terminal, recorder, message = self.attempt_with_field(
            "selection_token", CooperativeDriverValue()
        )
        self.assertGateRefusal(
            harness, terminal, recorder, message, "selection_token", str,
            "CooperativeDriverValue",
        )
        # The later reader, run for real rather than reasoned about.
        harness._rebind_model_evidence(terminal, "dispatch_f013")
        row = harness._terminals[terminal]
        self.assertEqual(
            {cell: row.get(cell) for cell in NO_MODEL_CELLS}, NO_MODEL_CELLS,
            "the terminal row carries model evidence the barrier refused",
        )

    def test_the_accepted_authority_holds_only_exact_primitives(self) -> None:
        """The positive control over a CONFORMING driver, and the accept path's invariant.

        A gate that refused everything would satisfy the methods above. This asserts the
        other side: an honest driver is accepted, and the canonical record the three maps
        share holds exactly-typed primitives in all fifteen fields -- so `request_evidence`,
        which is what `_rebind_model_evidence()` formats, renders instead of re-entering
        anything. It passes against the pre-fix tree for a conforming driver, which is
        what a positive control is for.
        """
        _recorder, harness = self.harness_for(InProcessModelDriver())
        terminal = self.session(harness, "worker")
        harness._verify_model_identity(
            task_id="task_exact", terminal=terminal, role="worker",
            phase="implementation", attempt=1, require_pair_admission=False,
        )
        record = harness._model_identity[("implementation", "worker")]
        self.assertIs(type(record), ModelEvidence)
        for field_name, field_type in self.ATTESTED_FIELD_TYPES:
            with self.subTest(field=field_name):
                self.assertIs(
                    type(getattr(record, field_name)), field_type,
                    f"{field_name} is "
                    f"{safe_type_name(getattr(record, field_name))}, not exactly "
                    f"{field_type.__name__}",
                )
        self.assertIsInstance(record.request_evidence, str)

    def test_the_barrier_reads_every_attested_field_exactly_once(self) -> None:
        """The fifteen-field read count the narrowed sibling's old name used to promise.

        `AllFieldReadCountingEvidence` instruments all fifteen fields instead of one, so a
        second read of ANY of them fails here. What it locks is the F-012 single-snapshot
        shape rather than the F-013 gate, and it therefore passes in both arms of the
        F-013 revert; its kill is demonstrated by a sibling mutation that re-reads one
        field off the driver's object in the accept block.
        """
        _recorder, harness = self.harness_for(InProcessModelDriver())
        terminal = self.session(harness, "worker")
        driver = StashingDriver(
            lambda t, rq, ob: AllFieldReadCountingEvidence.around(conforming(t, rq, ob))
        )
        harness.model_driver = driver
        harness._verify_model_identity(
            task_id="task_reads", terminal=terminal, role="worker",
            phase="implementation", attempt=1, require_pair_admission=False,
        )
        counts = driver.returned[0].counts
        self.assertEqual(
            counts,
            {name: 1 for name, _ in self.ATTESTED_FIELD_TYPES},
            "the barrier did not read each attested field off the driver exactly once",
        )


# ---- N-1b: the SETTLED-DISPATCH logging boundary ---------------------------------------

class RoutingRaisingDuringRowPreparation:
    """The real routing, except reading `schema_version` raises an ordinary `Exception`.

    This is the INDEPENDENT injection the review requires, and the independence is the
    point: it does not go through `observation_method`, so it stays reachable after N-1a
    closed the barrier. `_log_agent_identity_row()`'s `detail` assembly is the only site
    reachable from a dispatch that reads `schema_version` off the routing, so the failure
    lands squarely in the identity row's PREPARATION/RENDERING phase -- after settlement,
    before the writer call -- which is precisely the region the old guard did not cover.

    Everything else proxies to the real routing, so the barrier, `dispatch_context()` and
    every other consumer behave exactly as in the control run.
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @property
    def schema_version(self):
        raise RuntimeError("injected identity-row preparation failure")


class SettledDispatchLoggingBoundaryTests(ProvenanceTestCase):
    """N-1b. A non-authoritative identity-log failure may not unwind a settled dispatch.

    Deliberately NOT folded into the N-1a tests, and deliberately not skipped on the
    grounds that validating `observation_method` removed the reproduction route. The
    review is explicit about this: the barrier fix closes ONE way of reaching the
    unguarded region, and the unguarded region is the defect. So the failure is injected
    independently, through a route the barrier has no opinion about.

    Pre-fix, `_log_attempt()` called `_log_agent_identity_row()` directly and only the
    writer call at the bottom of that method was inside `_safe_log`. Everything above it
    -- the routing read, `_routing_is_model_aware()`, `_routing_key()`, the two
    `_model_identity` lookups and the `detail` assembly -- ran outside the guard, from a
    funnel `run_existing_task()` reaches AFTER `settle_attempt()` and BEFORE returning.
    """

    def control_and_injected(self, *, round_kind="phase_gate", role="worker",
                             phase="implementation", mode="complete"):
        """Run the SAME dispatch twice: once clean, once with the injection.

        Returning both is what makes the no-rollback / no-duplicate assertions sharp.
        "Nothing was rolled back and nothing happened twice" is a statement about the
        whole command stream and the whole delivery ledger, and the honest way to assert
        it is to compare against the run that did not fail, rather than to enumerate the
        handful of verbs a reviewer happens to think of.
        """
        results = []
        for injected in (False, True):
            recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
            run_id = f"run_inject_{int(injected)}"
            harness = self.build(
                recorder, routing=routing_from(SPLIT_PROFILE, "split"),
                model_driver=InProcessModelDriver(), run_id=run_id,
            )
            if injected:
                harness.agent_routing = RoutingRaisingDuringRowPreparation(
                    harness.agent_routing
                )
            attempt, _handle = self.dispatch(
                harness, recorder, role=role, mode=mode, phase=phase,
                round_kind=round_kind,
            )
            results.append((attempt, harness, recorder, run_id))
        return results

    def test_the_settled_result_is_returned_normally(self) -> None:
        """The headline. Pre-fix this raised `RuntimeError` out of `run_existing_task()`."""
        (control, _ch, _cr, _cid), (attempt, _h, _r, _id) = self.control_and_injected()
        self.assertEqual(attempt.outcome, control.outcome)
        self.assertEqual(attempt.settlement, control.settlement)
        self.assertEqual(attempt.outcome, "succeeded")
        self.assertEqual(attempt.settlement, "completed")
        self.assertEqual(attempt.lifecycle_action, control.lifecycle_action)

    def test_the_failure_is_recorded_in_logging_errors(self) -> None:
        """Absorbed is not the same as lost: it is named, under the operation's own label."""
        (_control, control_harness, _cr, _cid), (_a, harness, _r, _id) = (
            self.control_and_injected()
        )
        self.assertEqual(control_harness._logging_errors, [])
        self.assertEqual(len(harness._logging_errors), 1, harness._logging_errors)
        recorded = harness._logging_errors[0]
        self.assertIn("_log_agent_identity_row", recorded)
        self.assertIn("injected identity-row preparation failure", recorded)

    def test_the_subsequent_recording_steps_are_still_attempted(self) -> None:
        """The review's third bullet. One logging failure may not silence the rest.

        The identity row is the FIRST of four recording steps in `_log_attempt()`; the
        settled row, the timing row and (on a Final Review) the audit record all come
        after it. Pre-fix the exception unwound the funnel and none of them ran.
        """
        (_c, _ch, _cr, control_id), (_a, _h, _r, run_id) = self.control_and_injected()
        injected_events = [
            row["event"] for row in log_rows(self.artifact_dir, run_id)
        ]
        control_events = [
            row["event"] for row in log_rows(self.artifact_dir, control_id)
        ]
        # Exactly ONE row is missing -- the one that failed -- and every other row the
        # control run produced is still there, in the same order.
        self.assertIn(EVENT_AGENT_IDENTITY_BOUND, control_events)
        self.assertNotIn(EVENT_AGENT_IDENTITY_BOUND, injected_events)
        self.assertEqual(
            injected_events,
            [e for e in control_events if e != EVENT_AGENT_IDENTITY_BOUND],
        )
        # The TIMING row, which is a different file and a different writer, is written too.
        timing = run_logging.timing_log_path(run_id, base=self.artifact_dir)
        self.assertTrue(timing.exists())
        self.assertIn("dispatch_settled", timing.read_text(encoding="utf-8"))

    def test_the_final_review_audit_record_is_still_written(self) -> None:
        """The LAST step of the funnel, and the one furthest from the failure.

        A Final Review round adds `_log_final_review_audit()` after the two log rows, so
        it is the strongest evidence that the funnel ran to completion rather than merely
        surviving one more statement.
        """
        (_c, _ch, _cr, control_id), (_a, _h, _r, run_id) = self.control_and_injected(
            round_kind="final_review", role="reviewer", phase="final_review",
            mode="pass",
        )
        control_audit = run_logging.read_coordinator_audit(
            control_id, base=self.artifact_dir
        )
        injected_audit = run_logging.read_coordinator_audit(
            run_id, base=self.artifact_dir
        )
        self.assertEqual(
            [r["event"] for r in injected_audit],
            [r["event"] for r in control_audit],
            "the Coordinator audit of the injected run does not match the control",
        )
        self.assertNotEqual(injected_audit, [])

    def test_no_rollback_and_no_duplicate_dispatch_settlement_or_ack(self) -> None:
        """The whole lifecycle, compared against the control rather than spot-checked.

        If the identity-row failure had rolled anything back, retried anything, or turned
        an already-settled result into an apparent execution failure, the command stream
        or the delivery ledger would differ. Neither does.
        """
        (_c, control_harness, control_recorder, _cid), (
            _a, harness, recorder, _id
        ) = self.control_and_injected()
        self.assertEqual(recorder.verbs, control_recorder.verbs)
        # The two DISPATCHING verbs exactly once -- stated absolutely as well as against
        # the control, so a future change that moved BOTH sides together could not hide a
        # duplicate behind an equality that still held.
        for verb in ("worker-start", "task-create"):
            self.assertEqual(recorder.verbs.count(verb), 1, verb)
        # The settlement ledger: same deliveries, same terminal state.
        self.assertEqual(
            list(harness._deliveries), list(control_harness._deliveries)
        )
        for delivery_id, row in harness._deliveries.items():
            self.assertEqual(row, control_harness._deliveries[delivery_id], delivery_id)
        # SETTLEMENT and ACK, counted off the authoritative Coordinator audit rather than
        # off the command stream: that record is what a successor process recovers the
        # delivery ledger from, so it is where a rollback or a double-ack would show.
        audit = [
            r["event"] for r in run_logging.read_coordinator_audit(
                _id, base=self.artifact_dir
            )
        ]
        control_audit = [
            r["event"] for r in run_logging.read_coordinator_audit(
                _cid, base=self.artifact_dir
            )
        ]
        self.assertEqual(audit, control_audit)
        for event in set(audit):
            self.assertEqual(audit.count(event), 1, f"{event} was recorded twice")

    def test_an_interrupt_from_the_identity_row_still_propagates(self) -> None:
        """The F-002 line, re-asserted at the boundary this round widened the use of.

        `_safe_log` catches `Exception`, so wrapping the whole operation in it must not
        start swallowing control flow. All three control-flow types are checked, because
        the review names all three.
        """
        for control_flow in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(exception=control_flow.__name__):
                recorder = RecordingExec(
                    results={"check": RecordingExec.ACCEPTED_DONE}
                )
                run_id = f"run_cf_{control_flow.__name__}"
                harness = self.build(
                    recorder, routing=routing_from(SPLIT_PROFILE, "split"),
                    model_driver=InProcessModelDriver(), run_id=run_id,
                )

                def raising(**_kwargs):
                    raise control_flow

                harness._log_agent_identity_row = raising
                with self.assertRaises(control_flow):
                    self.dispatch(harness, recorder)
                self.assertEqual(harness._logging_errors, [])

    def test_the_authoritative_audit_family_is_still_fail_closed(self) -> None:
        """The review's explicit non-goal: do NOT blanket-wrap, do NOT swallow.

        `_audit_coordinator()` is the only source a restarted Coordinator can recover the
        delivery ledger from, and its publication failure must still stop the run. A fix
        that reached for `_safe_log` one level up -- around `_log_attempt()` itself --
        would silently take this away, and nothing else in the suite would notice.
        """
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(), run_id="run_audit_closed",
        )
        with patch.object(
            run_logging, "append_coordinator_audit_record",
            side_effect=OSError("audit volume is gone"),
        ):
            with self.assertRaises(OrcaRuntimeError) as caught:
                self.dispatch(harness, recorder)
        self.assertIn("coordinator audit record", str(caught.exception))
        self.assertTrue(
            any("append_coordinator_audit_record" in e
                for e in harness._logging_errors),
            harness._logging_errors,
        )

    def test_the_call_site_passes_the_whole_operation_through_the_guard(self) -> None:
        """Read off the source, so the one-line regression cannot come back quietly.

        The defect was not "a missing try"; it was a guard placed around the WRITE while
        the preparation sat outside it. A future edit that restores the direct call would
        pass every behavioural test above only as long as nothing in the preparation
        raises -- which is the state 9334b15 was already in.
        """
        import inspect

        source = inspect.getsource(OrcaRuntimeHarness._log_attempt)
        self.assertIn("self._safe_log(\n            self._log_agent_identity_row,", source)
        self.assertNotIn("        self._log_agent_identity_row(", source)


# ---- N-2: truth-value evaluation is inside the guard too -------------------------------

class ModelAwarenessTruthValueTests(unittest.TestCase):
    """N-2. `bool(aware)` sat OUTSIDE the `try`, so one of three shapes escaped raw.

    The helper reasons about exactly three ways the question can go unanswered -- the
    attribute is missing, the property raises, the returned value's `__bool__` raises --
    and pre-fix only the first two reached the conservative answer. The third escaped as
    a raw exception out of a `@staticmethod` predicate used by a fail-closed barrier and
    by the settled-dispatch logging funnel.
    """

    class RaisingBoolValue:
        def __bool__(self):
            raise RuntimeError("hostile __bool__ on is_model_aware")

    class AttributeRaisesBool:
        is_model_aware = None        # replaced in setUp; a plain attribute, not a property

    def test_a_raising_bool_is_treated_as_model_aware(self) -> None:
        """The reproduction. Pre-fix this raised `RuntimeError` instead of answering."""
        routing = self.AttributeRaisesBool()
        routing.is_model_aware = self.RaisingBoolValue()
        self.assertTrue(OrcaRuntimeHarness._routing_is_model_aware(routing))

    def test_the_three_unanswered_shapes_agree(self) -> None:
        """One fact, one answer. A missing attribute, a raising property and a raising
        `__bool__` are the same fact -- the question went unanswered -- so they must not
        produce three different outcomes."""

        class Missing:
            pass

        class RaisingProperty:
            @property
            def is_model_aware(self):
                raise RuntimeError("raising property")

        raising_bool = self.AttributeRaisesBool()
        raising_bool.is_model_aware = self.RaisingBoolValue()
        for label, routing in (
            ("missing attribute", Missing()),
            ("raising property", RaisingProperty()),
            ("raising __bool__", raising_bool),
        ):
            with self.subTest(shape=label):
                self.assertTrue(OrcaRuntimeHarness._routing_is_model_aware(routing))

    def test_the_supported_answers_are_untouched(self) -> None:
        """`None` stays the explicit legacy sentinel and real booleans still answer."""

        class Declares:
            def __init__(self, value):
                self.is_model_aware = value

        self.assertFalse(OrcaRuntimeHarness._routing_is_model_aware(None))
        self.assertTrue(OrcaRuntimeHarness._routing_is_model_aware(Declares(True)))
        self.assertFalse(OrcaRuntimeHarness._routing_is_model_aware(Declares(False)))
        real = routing_from(SPLIT_PROFILE, "split")
        self.assertEqual(
            OrcaRuntimeHarness._routing_is_model_aware(real), real.is_model_aware
        )

    def test_control_flow_propagates_from_both_the_property_and_the_bool(self) -> None:
        """The width is `Exception`, and the review says keep it there.

        Both legs are checked: a widening applied to only one of them would be the same
        half-fix shape this finding is about, mirrored.
        """
        for control_flow in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(exception=control_flow.__name__, leg="__bool__"):
                class InterruptingBool:
                    def __bool__(self):
                        raise control_flow

                routing = self.AttributeRaisesBool()
                routing.is_model_aware = InterruptingBool()
                with self.assertRaises(control_flow):
                    OrcaRuntimeHarness._routing_is_model_aware(routing)

            with self.subTest(exception=control_flow.__name__, leg="property"):
                class InterruptingProperty:
                    @property
                    def is_model_aware(self):
                        raise control_flow

                with self.assertRaises(control_flow):
                    OrcaRuntimeHarness._routing_is_model_aware(InterruptingProperty())

    def test_the_truth_value_evaluation_is_inside_the_try(self) -> None:
        """Read off the source: `return bool(aware)` must not drift back out of the guard.

        Asserted structurally with `ast` rather than by string matching, because the
        defect IS a structural one -- the statement was syntactically present and
        correct, and only its POSITION was wrong.
        """
        import inspect

        tree = ast.parse(
            textwrap.dedent(inspect.getsource(
                OrcaRuntimeHarness._routing_is_model_aware
            ))
        )
        tries = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
        self.assertEqual(len(tries), 1, "the guard's shape changed")
        guarded = ast.dump(ast.Module(body=tries[0].body, type_ignores=[]))
        self.assertIn("bool", guarded, "bool(aware) is outside the try again")
        # And the handler is Exception-wide, not BaseException-wide.
        handlers = [h.type.id for h in tries[0].handlers if isinstance(h.type, ast.Name)]
        self.assertEqual(handlers, ["Exception"])


# ---- N-3: what a fresh session does and does not recover -------------------------------

class FreshSessionScopeTests(ModelSessionTestCase):
    """N-3. A fresh session clears leg (k). It does not move a ROLE onto a new model.

    The wording this corrects implied otherwise, and a wording correction with no test is
    a correction that regresses on the next edit. The BEHAVIOUR here is unchanged and was
    already correct -- this class states it, so the comments and the code cannot drift
    apart again.
    """

    def test_a_fresh_session_must_still_resolve_to_the_roles_baseline(self) -> None:
        """The half the old wording left out, and the one that matters.

        S resolves model-b (accepted; the ROLE's baseline is now model-b), then drifts to
        model-a and is refused. A brand-new terminal for the same role that resolves to
        model-a is STILL refused -- by leg (i), on the role history, which moving sessions
        does not touch. Within one run, a different resolved model for a role requires a
        new run, not a new terminal.
        """
        # S: model-b then model-a. The FRESH session also resolves model-a -- the drifted
        # value -- which is the case the old wording read as recoverable.
        driver = sessions_resolving(["model-b", "model-a"], ["model-a"])
        _recorder, harness = self.harness_for(driver)
        spent = self.session(harness, "reviewer")
        harness.verify_model_identity(
            "task_rev", spent, role="reviewer", phase="implementation", attempt=1
        )
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_model_identity(
                "task_rev2", spent, role="reviewer", phase="implementation", attempt=2
            )
        fresh = self.session(harness, "reviewer")
        self.assertNotEqual(fresh, spent)
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.verify_model_identity(
                "task_rev3", fresh, role="reviewer", phase="implementation", attempt=3
            )
        message = str(caught.exception)
        self.assertTrue(
            message.startswith(MODEL_SELECTION_AMBIGUOUS),
            f"expected {MODEL_SELECTION_AMBIGUOUS}, got: {message}",
        )
        # Refused on the ROLE's baseline, which names model-b -- not on anything about
        # the terminal, which this run has never seen before.
        self.assertIn("model-b", message)
        self.assertEqual(
            harness._model_role_history[("implementation", "reviewer")], "model-b"
        )
        self.assertNoRecordFor(harness, fresh, role="reviewer")

    def test_the_production_comments_carry_the_qualification(self) -> None:
        """Both sites, asserted against the source, so the prose cannot quietly revert."""
        import inspect

        for subject in (
            OrcaRuntimeHarness._stale_model_evidence,
            OrcaRuntimeHarness._verify_model_identity,
        ):
            source = inspect.getsource(subject).lower()
            self.assertIn("baseline", source, subject.__name__)
            self.assertIn("new run", source, subject.__name__)


# ---- N-4: the totality claims say what the code actually does --------------------------

class TotalityClaimAccuracyTests(unittest.TestCase):
    """N-4. Unconditional "TOTAL" / "Never raises" claims the code stopped honouring.

    Review F-002 narrowed every render guard in this module from `BaseException` to
    `Exception` -- correctly, because an operator's Ctrl-C arriving inside a hostile
    `__str__` is not a rendering failure to be papered over. The DOCUMENTATION was not
    narrowed with it, so a set of places promised totality the code does not provide.

    HISTORY, stated rather than presented as new: the Coordinator was told about this as
    a non-blocking note in the previous round (R10 / F-004) and deliberately deferred it.
    This review escalates it and names five locations where three were flagged, so the
    deferral is superseded. The Coordinator then ruled that N-4's "correct ALL" heading
    reaches three same-class siblings beyond those five, which is why `INVENTORY` below
    holds eight entries and not five.

    Behaviour is deliberately untouched. The tests below, TAKEN TOGETHER rather than one
    by one, assert BOTH halves -- that the prose now states the split, and that no
    capture was widened to make the old prose true, which is the tempting wrong fix and
    is the F-002 defect restored. No single method below asserts both halves, and
    iteration 4 made that collective reading explicit rather than leave a sentence whose
    strict reading is false (review F-004's lesson, applied before it was asked for).

    THE RULE, AND NOTHING WIDER (OS-49 BUGFIX iterations 2-3, reviews F-002 and F-003).
    Two statements, both literally true of this class and the second one machine-checked:

      1. a site that is not in `INVENTORY` is not claimed by this class; and
      2. every test method in this class references `INVENTORY`, directly or through its
         two resolution points `subjects()` / `sources()`.

    What is DELIBERATELY NOT CLAIMED, because iteration 2 claimed it and F-003 was the
    result: that every inventory site is inspected by every test, and that every test
    derives its SUBJECTS from the inventory. Neither is true -- the rule-enforcing
    meta-test's subject is this class's own source, not a harness site -- so neither is
    stated. Statement 2 is the honest invariant, and
    `test_every_test_method_in_this_class_derives_from_the_inventory` enforces it by AST
    inspection of this class's own methods: the rule is an assertion the suite runs, not
    prose a reader has to audit. That is the difference between this iteration and the
    previous two, both of which failed on prose outrunning assertions.

    A test whose subject is a renderer helper that is NOT an inventory site belongs in
    `RendererHelperBehaviourTests` below, not here. Deriving such a test over eight sites
    would manufacture a generalization that does not exist, which is the F-002/F-003
    defect wearing a structural disguise; honest separation beats fake derivation.
    """

    #: THE inventory -- one constant, eight sites.
    #: Five are the locations the review lists; three (`_bind_turn_boundary_session`,
    #: `_begin_turn_boundary_liveness`, `_end_turn_boundary_liveness`) are the same-class
    #: siblings the Coordinator ruled in scope under N-4's own "correct ALL unconditional
    #: totality claims" heading. Each entry is (label, holder, attribute); holder is
    #: "module" for a module-level function and "harness" for an `OrcaRuntimeHarness`
    #: method.
    #:
    #: OS-49 BUGFIX iteration 2 (review F-002). This REPLACES a four-entry `NAMED` tuple
    #: that sat directly beneath a comment asserting the five-plus-three superset. The
    #: comment named the sites; nothing iterated them. So three sites were claimed and
    #: never inspected, and the surviving unconditional claim inside
    #: `_bind_turn_boundary_session` (iteration-2 F-001) went unseen -- a sibling could
    #: be reverted whole and the class still passed completely. The fix is structural
    #: rather than a longer comment: THIS tuple is what the class's site-level tests
    #: iterate, through `subjects()` / `sources()`, so a site cannot be claimed in prose
    #: and left uninspected. `test_the_inventory_covers_every_site_the_claim_names` locks
    #: its membership so it cannot shrink back silently.
    #:
    #: OS-49 BUGFIX iteration 3 (review F-003). This comment previously said "every test
    #: below derives its subjects from THIS tuple", which was false of two of the then
    #: seven methods. The claim is now the narrower one stated above and in the class
    #: docstring, and the reference rule is enforced by
    #: `test_every_test_method_in_this_class_derives_from_the_inventory` instead of
    #: being asserted here in prose.
    INVENTORY = (
        ("safe_text", "module", "safe_text"),
        ("safe_type_name", "module", "safe_type_name"),
        ("safe_exception_text", "module", "safe_exception_text"),
        ("_writer_label", "module", "_writer_label"),
        ("_safe_log", "harness", "_safe_log"),
        ("_bind_turn_boundary_session", "harness", "_bind_turn_boundary_session"),
        ("_begin_turn_boundary_liveness", "harness", "_begin_turn_boundary_liveness"),
        ("_end_turn_boundary_liveness", "harness", "_end_turn_boundary_liveness"),
    )

    #: The phrases a wording finding is about, banned over the WHOLE source of every
    #: inventory site -- docstring AND inline commentary. Iteration 1 checked only the
    #: docstring head, which is precisely why F-001's surviving inline claim survived.
    FORBIDDEN_CLAIMS = (
        "TOTAL. Never raises",
        "-- TOTAL.",
        "-- TOTAL,",
        "Never raises",
        "never-raises",
    )

    #: The tokens that count as "references `INVENTORY`" for statement 2 of the class
    #: docstring: the constant itself, or either of its two resolution points. This is
    #: the rule's vocabulary, read by
    #: `test_every_test_method_in_this_class_derives_from_the_inventory`.
    INVENTORY_REFERENCES = ("INVENTORY", "subjects", "sources")

    def subjects(self):
        """Resolve `INVENTORY` to (label, object). The single resolution point."""
        from scripts import orca_runtime_harness as harness_module

        for label, holder, attribute in self.INVENTORY:
            owner = harness_module if holder == "module" else OrcaRuntimeHarness
            self.assertTrue(
                hasattr(owner, attribute),
                f"inventory names {label}, which {owner!r} does not have",
            )
            yield label, getattr(owner, attribute)

    def sources(self):
        import inspect

        for label, subject in self.subjects():
            yield label, inspect.getsource(subject)

    @staticmethod
    def _comment_blocks(source: str):
        """The contiguous `#` comment blocks of a source, docstrings excluded.

        F-001 lived in one of these, not in a docstring, so the assertion surface has
        to include them explicitly.
        """
        blocks: list[str] = []
        current: list[str] = []
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                current.append(stripped.lstrip("#").strip())
                continue
            if current:
                blocks.append(" ".join(current))
                current = []
        if current:
            blocks.append(" ".join(current))
        return blocks

    def test_the_inventory_covers_every_site_the_claim_names(self) -> None:
        """The inventory IS the claim, so the claim is what gets asserted.

        Iteration-2 F-002: a comment that names sites is not a mechanism. This pins the
        membership the rest of the class iterates, so dropping a sibling back out of
        coverage fails here instead of passing silently.
        """
        self.assertEqual(
            {label for label, _holder, _attribute in self.INVENTORY},
            {
                "safe_text",
                "safe_type_name",
                "safe_exception_text",
                "_writer_label",
                "_safe_log",
                "_bind_turn_boundary_session",
                "_begin_turn_boundary_liveness",
                "_end_turn_boundary_liveness",
            },
        )
        self.assertEqual(len(self.INVENTORY), 8)
        self.assertEqual(len(list(self.subjects())), 8)
        self.assertEqual(len(list(self.sources())), 8)

    def test_no_unconditional_totality_claim_survives(self) -> None:
        """The exact phrases, which is what a wording finding is about.

        Over the whole source of every inventory site, docstring and inline comments
        alike -- the surface iteration 1 restricted to the docstring head.
        """
        inspected = 0
        for name, source in self.sources():
            with self.subTest(location=name):
                for phrase in self.FORBIDDEN_CLAIMS:
                    self.assertNotIn(phrase, source, f"{name}: {phrase!r}")
                inspected += 1
        self.assertEqual(inspected, len(self.INVENTORY))

    def test_every_location_states_the_actual_split(self) -> None:
        """Not merely the removal of a word: the replacement has to say what is true."""
        inspected = 0
        for name, source in self.sources():
            with self.subTest(location=name):
                self.assertIn("Exception", source, name)
                self.assertTrue(
                    "propagate" in source or "PROPAGATE" in source,
                    f"{name} does not say that control flow propagates",
                )
                inspected += 1
        self.assertEqual(inspected, len(self.INVENTORY))

    def test_every_inline_claim_of_the_shared_discipline_is_qualified(self) -> None:
        """F-001 itself, locked where it actually lived.

        A comment block that invokes the module's shared "discipline" is making the same
        totality claim the docstrings make, so it owes the same qualification: name the
        capture and say that control flow propagates. The block F-001 flagged said
        "Under the same never-raises discipline as the binding" and said neither.
        """
        seen = 0
        for name, source in self.sources():
            for block in self._comment_blocks(source):
                if "discipline" not in block.lower():
                    continue
                seen += 1
                with self.subTest(location=name, block=block[:60]):
                    self.assertIn("Exception", block, f"{name}: {block}")
                    self.assertIn("propagate", block, f"{name}: {block}")
        self.assertGreaterEqual(
            seen, 1, "no inline discipline claim found; F-001's surface has moved"
        )

    def test_no_capture_was_widened_to_make_the_old_wording_true(self) -> None:
        """The wrong fix, forbidden structurally.

        `safe_exception_text`'s PARAMETER is annotated `BaseException` -- it renders one
        -- so the check is on `except` handlers, not on the word. An AST handler census
        over all eight inventory sites, which is also the iteration-2 evidence that the
        comment-only edits moved no control flow.
        """
        import inspect

        census: dict[str, list[str]] = {}
        for name, subject in self.subjects():
            with self.subTest(location=name):
                tree = ast.parse(textwrap.dedent(inspect.getsource(subject)))
                widths = []
                for handler in [
                    n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)
                ]:
                    self.assertIsInstance(handler.type, ast.Name)
                    self.assertEqual(
                        handler.type.id, "Exception",
                        f"{name} catches "
                        f"{getattr(handler.type, 'id', handler.type)}; widening to "
                        "BaseException reintroduces the F-002 interrupt defect",
                    )
                    widths.append(handler.type.id)
                census[name] = widths
        self.assertEqual(len(census), len(self.INVENTORY))
        self.assertEqual(
            {width for widths in census.values() for width in widths}, {"Exception"}
        )

    def test_every_test_method_in_this_class_derives_from_the_inventory(self) -> None:
        """Statement 2 of the class docstring, enforced instead of asserted.

        OS-49 BUGFIX iteration 3, review F-003. This is the whole point of the
        iteration. Iteration 2's class prose claimed inventory-derived coverage that two
        of its seven methods did not have, which is the same shape as F-002 one level
        up: a claim a reader must audit by hand, and nobody did. Rewording it a third
        time would have invited an eighth recurrence, so the claim is now a test.

        The technique is the Reviewer's own: parse this class and census which of its
        test methods reach `INVENTORY`, by name or through `subjects()` / `sources()`.
        Any test method that does not is a failure HERE, named in the message, rather
        than a silent widening of what the docstring promises. A test whose subject is
        not an inventory site belongs in `RendererHelperBehaviourTests`.

        Note what this test's own subject is: this class's source text, not a harness
        site. That is exactly why the docstring does not claim every test derives its
        SUBJECTS from the inventory -- this one does not, and claiming otherwise would
        be the defect again.
        """
        import inspect

        # The token set is only a proxy for the rule if the tokens resolve to something
        # real, so pin them against the live class before trusting a census of them.
        self.assertEqual(len(self.INVENTORY), 8)
        for token in self.INVENTORY_REFERENCES:
            self.assertTrue(
                hasattr(type(self), token),
                f"the rule names {token!r}, which {type(self).__name__} does not define;"
                " the reference vocabulary has drifted from the class",
            )

        tree = ast.parse(textwrap.dedent(inspect.getsource(type(self))))
        class_def = tree.body[0]
        self.assertIsInstance(class_def, ast.ClassDef)

        expected = set(self.INVENTORY_REFERENCES)
        methods = [
            node
            for node in class_def.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test")
        ]
        deriving: list[str] = []
        non_deriving: list[str] = []
        for method in methods:
            referenced = {
                node.id for node in ast.walk(method) if isinstance(node, ast.Name)
            } | {
                node.attr for node in ast.walk(method) if isinstance(node, ast.Attribute)
            }
            with self.subTest(method=method.name):
                hit = sorted(referenced & expected)
                self.assertTrue(
                    hit,
                    f"{method.name} is a test in the inventory class but references "
                    f"none of {sorted(expected)}; either derive it from the inventory "
                    "or move it to RendererHelperBehaviourTests -- do not widen the "
                    "class prose to cover it (review F-003)",
                )
            (deriving if hit else non_deriving).append(method.name)

        self.assertEqual(
            non_deriving,
            [],
            f"{len(non_deriving)} of {len(methods)} test methods do not reach the "
            f"inventory: {non_deriving}",
        )
        self.assertEqual(len(deriving), len(methods))
        # A census that found nothing would pass vacuously, which is the F-002 failure
        # mode in miniature. This class has six tests; fewer means methods vanished.
        self.assertGreaterEqual(len(methods), 6, f"census saw only {len(methods)} tests")


class RendererHelperBehaviourTests(unittest.TestCase):
    """N-4 companion: the renderer-helper behaviour the narrowed prose describes.

    DELIBERATELY NOT IN `TotalityClaimAccuracyTests` (OS-49 BUGFIX iteration 3, review
    F-003). This class reaches `safe_text` and `safe_repr` and no other helper, and it
    does not reach the two equally: the split test covers `safe_text` AND `safe_repr`,
    while the byte-identity test covers `safe_repr` alone. That distribution is stated
    rather than averaged away because OS-49 BUGFIX iteration 4, review F-004, found the
    earlier wording quantifying two helpers over its two methods when one of the two
    methods touches one helper. (A class docstring carrying that sentence would itself be
    the defect, so it lives as a specimen string in `ClassDocstringClaimShapeTests` below,
    where the shape is checked.)
    The fix is the sentence, not the tests: forcing a `safe_text` assertion into the
    byte-identity test to make the old wording true would be F-002's disease
    (manufacturing coverage so prose reads as honest) in a new costume. `safe_repr` is
    NOT an inventory site -- it is iteration 1's new helper -- so "deriving" a
    byte-identity or interrupt-propagation test over the eight inventory sites would
    manufacture a generalization that does not exist: the eight sites do not share a
    return contract, and asserting one over them would either be vacuous or require
    special-casing per site. Inventing that derivation to satisfy a sentence is the same
    disease as F-002 (prose claiming coverage the code lacked) and F-003 (a structural
    fix claiming more than its own tests ran). Honest separation beats fake derivation,
    so these two live here, under a name that says which helpers they check.

    What the inventory class asserts is that the eight sites' PROSE states the
    `Exception`/propagate split and that no handler was widened to make the old wording
    true. What this class asserts is that the two helpers whose prose was rewritten
    actually behave that way. Neither claims the other's surface.
    """

    def test_safe_text_and_safe_repr_render_ordinary_failures_and_propagate_interrupts(
        self,
    ) -> None:
        """The split, on the two helpers -- not on the eight inventory sites.

        Renamed from `test_the_behaviour_the_prose_now_describes_is_the_behaviour`
        (review F-003): the old name claimed the class's whole described behaviour while
        checking two helpers.
        """

        class OrdinaryFailure:
            def __str__(self):
                raise RuntimeError("no")

            def __repr__(self):
                raise RuntimeError("no")

        class Interrupting:
            def __str__(self):
                raise KeyboardInterrupt

            def __repr__(self):
                raise KeyboardInterrupt

        self.assertEqual(safe_text(OrdinaryFailure()), UNRENDERABLE_TEXT)
        self.assertEqual(safe_repr(OrdinaryFailure()), UNRENDERABLE_REPR)
        with self.assertRaises(KeyboardInterrupt):
            safe_text(Interrupting())
        with self.assertRaises(KeyboardInterrupt):
            safe_repr(Interrupting())

    def test_safe_repr_is_byte_identical_to_the_eager_form_when_it_renders(self) -> None:
        """The compatibility claim the refusal messages rest on."""
        for value in ("a string", 7, None, ("t", 1), {"k": "v"}, b"bytes", 1.5):
            with self.subTest(value=value):
                self.assertEqual(safe_repr(value), f"{value!r}")


class ClassDocstringClaimShapeTests(unittest.TestCase):
    """OS-49 BUGFIX iteration 4, review F-004. The claim SHAPE that failed three times.

    F-002, F-003 and F-004 are one defect in three costumes, and the costume was always
    the same: an aggregate coverage sentence in whichever test class was newest, putting
    a universal quantifier across a class's methods and asserting a surface that at
    least one of those methods never executed. Three rounds running, the sentence was
    rewritten by hand and the next round grew a fresh one somewhere else. A fourth
    hand-rewrite buys a fourth round, so iteration 4 stops rewriting sentences and
    starts checking the shape.

    ITERATION 5, review R20, extends the SCOPE and leaves the vocabulary alone. The
    Final Adversarial Review found a tenth instance of the same defect in this file's
    own MODULE docstring, which promised a baseline-failure experiment covering the
    whole file. `FORBIDDEN_CLAIM_SHAPES` already contained that phrasing; what let the
    sentence live was the limits paragraph below, which excluded the module docstring
    from the census. The census now covers it, so the class name records where this
    guard started rather than every surface it now reads --
    `test_no_unenforced_aggregate_coverage_claim_survives_the_module_docstring` is the
    method that covers the added surface.

    THE RULE. A class docstring in THIS FILE may carry one of the quantifier shapes in
    `FORBIDDEN_CLAIM_SHAPES` only if that same docstring also names a `test_...` method
    that really exists on that same class -- that is, only if the quantified claim comes
    with a pointer to an assertion that enforces it, instead of asking a reader to audit
    it by hand. Nobody audited it by hand three times in a row. `TotalityClaimAccuracyTests`
    is the worked example of the permitted form: it quantifies over its own methods and
    names `test_every_test_method_in_this_class_derives_from_the_inventory`, which runs
    the census. `RendererHelperBehaviourTests` is the worked example of the other way
    out -- drop the quantifier and state the actual per-method distribution.

    THE SAME RULE AT MODULE LEVEL, which needs a decision of its own because a module
    docstring has no owning class whose method census could bound a quantifier. Most
    module prose legitimately points at no `test_...` method whatsoever, and that is the
    normal case, not a loophole: it is history -- which review comment produced which
    block of this file, and what the pre-fix failure mode was. Such prose is untouched
    here, because this check fires only on an aggregate quantifier and history needs
    none. When the module docstring DOES quantify over this file's test methods, the
    pointer it must carry is resolved against the UNION of the `test_...` method names
    this file defines, because a module-level quantifier ranges over the whole file
    instead of over one class. A `test_...` token matching nothing in that union buys
    nothing, exactly as at class level. The hatch therefore stays open and stays
    meaningful: a module sentence may quantify only if it names the live assertion
    enforcing it.

    WHAT THIS CHECK DOES NOT DO, said plainly, because a guard against overclaiming that
    overclaimed about itself would be the ninth instance of the defect it exists to stop:

      * It matches a FIXED LIST of phrasings. An aggregate claim worded some other way
        passes it untouched. The list is the shapes this file has actually produced plus
        their nearest siblings, not a theory of English.
      * It never reads the named method. It cannot tell whether that method enforces the
        quantified claim or merely shares the file with it; it only confirms the
        docstring points at something real on the same class.
      * It scans CLASS docstrings and THIS FILE'S MODULE docstring. It does not scan
        per-method docstrings, comments, or string data held in method bodies. Before
        iteration 5 it excluded the module docstring, and review R20 is what that
        exclusion cost. Per-method docstrings, comments and string data in method bodies
        remain uncovered, and a claim worded into one of them survives this check.

    So this proves nothing about the absence of overclaims in this file, and must not be
    read as proving it. What it does is remove ONE shape -- the shape that has now failed
    three consecutive reviews -- from the set of defects that depend on a human noticing.
    Its first catch was not F-004: it was `RunBoundaryModelStateTests`, whose committed
    docstring stretched the quantifier across four methods when the fourth drives no run
    at all and reads source text instead. That one had survived every review of this PR.
    The sentence above was rewritten because the scan flagged THIS docstring on its first
    run.

    The mechanism is deliberately the one already proven at the eight N-4 sites,
    `TotalityClaimAccuracyTests.FORBIDDEN_CLAIMS`: a banned-phrase scan over source text.
    Reusing it instead of inventing a second mechanism keeps one idea in the file applied
    to two surfaces.
    """

    #: The banned quantifier shapes, as regexes matched case-insensitively against a
    #: class docstring. Every entry is either a phrasing this file actually produced and
    #: a review found to outrun its methods, or that phrasing's nearest sibling. The
    #: offending sentences themselves are specimens in
    #: `test_the_shape_vocabulary_catches_the_sentences_that_actually_failed`, held as
    #: STRING DATA in a method body -- a class docstring carrying one would be the defect.
    FORBIDDEN_CLAIM_SHAPES = (
        r"\bboth\s+(?:of\s+(?:the|its|these)\s+)?tests?\b",
        r"\bboth\s+(?:of\s+(?:the|its|these)\s+)?methods?\b",
        r"\bboth\s+assertions\b",
        r"\bevery\s+test\b",
        r"\beach\s+(?:of\s+(?:the|its|these)\s+)?tests?\b",
        r"\ball\s+(?:of\s+(?:the|its|these)\s+)?tests?\b",
        r"\bthe\s+(?:two|three|four)?\s*tests?\s+below\b",
        r"\bneither\s+test\b",
        # ITERATION 7, review F-006: class-scoped quantifiers. They bite on CLASS
        # docstrings, where `enforcing` is bounded to the class's own methods. They do
        # NOT lock the module docstring -- the module-level hatch is already satisfied
        # for this file -- and the F-006 falsehoods are locked by
        # `Population3ReadSubjectFactTests` instead.
        r"\bevery\s+class(?:es)?\b",
        r"\beach\s+(?:of\s+(?:the|its|these)\s+)?class(?:es)?\b",
        r"\ball\s+(?:of\s+(?:the|its|these)\s+)?classes\b",
        r"\bboth\s+(?:of\s+(?:the|its|these)\s+)?classes\b",
    )

    #: Anti-vacuity floor. A census that found no class docstrings would satisfy a
    #: banned-phrase scan for the exact F-002 reason -- nothing inspected, so nothing can
    #: fail -- so the scan is pinned against the live file. Twenty test classes exist as
    #: of iteration 4; the floor rises only when classes are deliberately added.
    MINIMUM_TEST_CLASSES = 20

    #: The classes this finding is about, which must be IN the census or the census is
    #: not scanning what the rule claims to cover.
    REQUIRED_IN_CENSUS = (
        "TotalityClaimAccuracyTests",
        "RendererHelperBehaviourTests",
        "RunBoundaryModelStateTests",
        "ClassDocstringClaimShapeTests",
    )

    @staticmethod
    def _test_classes():
        """Yield (class name, class docstring, defined method names) for THIS file.

        A class counts as a test class when its name ends in `Tests` or it defines a
        `test`-prefixed method, which is the same population `unittest` collects from
        here. The subject is this file's own source text, read from disk and parsed --
        the technique `test_every_test_method_in_this_class_derives_from_the_inventory`
        already uses one level down.
        """
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            methods = {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if not (
                node.name.endswith("Tests")
                or any(name.startswith("test") for name in methods)
            ):
                continue
            yield node.name, ast.get_docstring(node) or "", methods

    @classmethod
    def _module_surface(cls):
        """Return (module docstring, the `test_...` names defined anywhere in THIS file).

        The second element is the union the module-level hatch resolves against. It is
        built from the same `_test_classes()` population the class-level rule walks, so
        the two surfaces cannot drift apart over what counts as a real test method.
        """
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        defined: set[str] = set()
        for _name, _doc, methods in cls._test_classes():
            defined |= {name for name in methods if name.startswith("test_")}
        return ast.get_docstring(tree) or "", defined

    @classmethod
    def _matched_shapes(cls, doc: str) -> list[str]:
        """The banned quantifier shapes present in `doc`, as matched text."""
        return [
            match.group(0)
            for match in (
                re.search(shape, doc, re.IGNORECASE)
                for shape in cls.FORBIDDEN_CLAIM_SHAPES
            )
            if match is not None
        ]

    def test_the_census_sees_the_file_it_claims_to_scan(self) -> None:
        """Anti-vacuity first, because a scan of nothing passes a banned-phrase check.

        F-002 in miniature: the previous iterations' failures were all claims that no
        assertion reached. A banned-phrase scan over an empty population is exactly
        that, so the population is pinned before it is trusted.
        """
        census = {name: doc for name, doc, _methods in self._test_classes()}
        self.assertGreaterEqual(
            len(census),
            self.MINIMUM_TEST_CLASSES,
            f"census saw only {len(census)} test classes; the AST scan has stopped "
            "reaching this file's classes and would pass vacuously",
        )
        for required in self.REQUIRED_IN_CENSUS:
            with self.subTest(test_class=required):
                self.assertIn(required, census)
                self.assertTrue(
                    census[required].strip(),
                    f"{required} has no class docstring, so the scan asserts nothing "
                    "about it",
                )

    def test_the_shape_vocabulary_catches_the_sentences_that_actually_failed(self) -> None:
        """The patterns are a guard only if they match the prose found wrong.

        These are the real sentences from iterations 2 and 3 and from
        `RunBoundaryModelStateTests`, carried as STRING DATA in a method body rather
        than as docstring prose -- a class docstring holding one of them is the defect
        this class exists to fail on, while a specimen in a list is the evidence that
        the vocabulary is alive. Deleting a pattern to make a docstring pass fails here.
        """
        specimens = (
            "Both tests below exercise `safe_text` and `safe_repr` only.",
            "every test below derives its subjects from THIS tuple",
            "Every test here drives the REAL start_run() twice with NO finish() between",
            "The tests below assert BOTH halves",
            "All of the tests in this class cover the inventory",
            "Each test asserts the split",
            # ITERATION 7, review F-006. The first is the real sentence F-005 found
            # false in this file's module docstring; the rest are its nearest siblings
            # for the three other class-scoped shapes added above.
            "Each class names the finding it locks and states the pre-fix failure mode",
            "every class here names the finding it locks",
            "All of the classes below name the finding they lock",
            "both classes name the finding they lock",
        )
        for specimen in specimens:
            with self.subTest(specimen=specimen):
                self.assertTrue(
                    any(
                        re.search(shape, specimen, re.IGNORECASE)
                        for shape in self.FORBIDDEN_CLAIM_SHAPES
                    ),
                    f"no shape in FORBIDDEN_CLAIM_SHAPES matches {specimen!r}; the "
                    "vocabulary no longer covers a phrasing that already failed review",
                )

    def test_no_unenforced_aggregate_coverage_claim_survives_a_class_docstring(
        self,
    ) -> None:
        """THE rule, over every test class docstring in this file.

        An aggregate quantifier across a class's methods is permitted only alongside the
        name of a real `test_...` method of that class, so the claim points at machine
        enforcement instead of at a reader's goodwill. Anything else names the offending
        class and the matched phrase here, which is where F-002, F-003 and F-004 would
        each have been caught before a review had to find them.
        """
        inspected = 0
        offenders: list[tuple[str, str]] = []
        for name, doc, methods in self._test_classes():
            inspected += 1
            enforcing = {
                token
                for token in re.findall(r"\btest_[A-Za-z0-9_]+", doc)
                if token in methods
            }
            matched = [
                match.group(0)
                for match in (
                    re.search(shape, doc, re.IGNORECASE)
                    for shape in self.FORBIDDEN_CLAIM_SHAPES
                )
                if match is not None
            ]
            with self.subTest(test_class=name):
                if matched and not enforcing:
                    offenders.append((name, matched[0]))
                    self.fail(
                        f"{name}'s class docstring makes an aggregate per-test coverage "
                        f"claim ({matched[0]!r}) and names no test method of {name} that "
                        "enforces it. State the actual per-method distribution instead, "
                        "or name the test that checks the quantified claim -- do NOT add "
                        "an assertion to a method just to make the sentence true "
                        "(reviews F-002, F-003, F-004)"
                    )
        self.assertGreaterEqual(
            inspected,
            self.MINIMUM_TEST_CLASSES,
            f"scan inspected only {inspected} class docstrings",
        )
        self.assertEqual(offenders, [])

    def test_no_unenforced_aggregate_coverage_claim_survives_the_module_docstring(
        self,
    ) -> None:
        """THE rule again, on the surface review R20 found the tenth instance on.

        The module header is where this file explains which review comment produced
        which block and what each pre-fix failure mode was, and this check fires only on
        an aggregate quantifier, which history does not need. If it ever quantifies over
        this file's test methods again, it has to name one that exists -- resolved
        file-wide, per the module-level
        reading in this class's docstring -- so that the sentence points at an assertion
        rather than at a reader's goodwill. R20's own sentence, "Every test here is
        written so that it FAILS against HEAD e5dead8 and passes after", named nothing
        and could not be demonstrated: an isolated e5dead8 tree overlaid with this file
        collects ZERO tests, because that baseline has no
        `MODEL_SELECTION_OBSERVATION_METHODS` to import.
        """
        doc, defined = self._module_surface()
        self.assertTrue(
            doc.strip(),
            "this file has no module docstring, so this check asserts nothing; it was "
            "added because a universal claim lived in that docstring unchallenged "
            "through four iterations and a Final Adversarial Review (R20)",
        )
        self.assertGreaterEqual(
            len(defined),
            self.MINIMUM_TEST_CLASSES,
            f"the file-wide hatch resolves against only {len(defined)} test methods; "
            "the AST scan has stopped reaching this file and the pointer check would "
            "admit any name at all",
        )
        enforcing = {
            token for token in re.findall(r"\btest_[A-Za-z0-9_]+", doc)
            if token in defined
        }
        matched = self._matched_shapes(doc)
        if matched and not enforcing:
            self.fail(
                f"the MODULE docstring makes an aggregate per-test coverage claim "
                f"({matched[0]!r}) and names no test method of this file that enforces "
                "it. State the scoped history instead -- which review comment produced "
                "which block, and what was actually demonstrated for it -- or name the "
                "test that checks the quantified claim. Do NOT widen a test to make the "
                "sentence true (review R20, and F-002/F-003/F-004 before it)"
            )


class Population3ReadSubjectFactTests(unittest.TestCase):
    """OS-49 BUGFIX iteration 7, review F-006; narrowed in iteration 8, review F-007.

    The claim kind a phrase list cannot tell true from false.

    F-005 was three false sentences in this file's MODULE docstring, and every one of
    them survived `ClassDocstringClaimShapeTests` untouched. The iteration-6 review
    established that by restoring the exact pre-F-005 header into an isolated copy: 96
    tests collected, and all four of that class's assertions passed, 30 subtests. That
    experiment no longer reproduces unchanged on THIS file, because a FULL restoration
    also deletes the pointer sentence that holds the module-level hatch open, so the
    `Each class` shape added in iteration 7 bites as well -- which is why each falsehood
    below is demonstrated RESTORED ALONE, where the pointer survives, the hatch stays
    open and the vocabulary stays inert. What F-005 showed is not a hole in that
    vocabulary, it is the wrong instrument. Two of the three sentences are not quantifier defects at all. One
    attributed a single shared source-reading subject to a group of classes whose
    derived subjects differ; one declared that group unreachable from production, of a
    group containing a class that reads production source on purpose; only the third was
    a quantifier, stretching this file's finding-naming drafting convention across
    classes, three of which carry no docstring whatsoever. A fixed list of phrasings is
    powerless against a false statement of fact, because the phrasing is not what is
    wrong with it. The only thing that contradicts a fact is the fact.

    WHAT THIS CLASS DOES. It walks THIS file's live AST and records, per top-level
    class, a LABEL SET over `PRODUCTION_SOURCE`, `OWN_CLASS_SOURCE`,
    `THIS_FILE_SOURCE`, `OTHER_SOURCE` and `NO_SOURCE` -- `_read_subject` below is the
    whole definition of which call shape yields which -- plus whether the class carries
    a docstring and whether that docstring names a finding identifier. A label SET may
    hold more than one label: `TotalityClaimAccuracyTests` holds
    `{OWN_CLASS_SOURCE, PRODUCTION_SOURCE}`. It then reads the MODULE docstring and
    fails when a claim of one of three RECOGNISED KINDS contradicts that census:

      * `test_no_joint_read_subject_claim_survives_the_module_docstring` fails when one
        recognised plural read attribution covers two or more classes whose derived
        label SETS are not equal as whole sets.
      * `test_no_production_invariance_claim_survives_the_module_docstring` fails when a
        recognised claim that production cannot move something shares a block with a
        class whose label SET CONTAINS `PRODUCTION_SOURCE`.
      * `test_no_unsupported_class_universal_survives_the_module_docstring` fails when a
        recognised class-scoped universal quantifier and a recognised finding-naming
        phrasing occur in one block within 120 characters of one another, in either
        order, while the derived census still holds a counterexample.

    WHAT THE JOINT COMPARISON IS NOT. It is WHOLE-SET EQUALITY of the derived label
    sets, which is neither necessary nor sufficient for a one-subject attribution to be
    true. It can REJECT a true attribution: review F-008 demonstrated that at a valid
    101-test collection, where a class holding `{OWN_CLASS_SOURCE, PRODUCTION_SOURCE}`
    and a class holding `{PRODUCTION_SOURCE}` were truthfully said to read production
    harness source and the comparison failed on the set inequality alone. It can ADMIT a
    false one: review F-007 demonstrated that at a valid 101-test collection, with a
    recognised joint attribution naming production harness source over two classes that
    each held `{THIS_FILE_SOURCE}`. The asserted subject is never parsed, extracted or
    compared. Nothing here is proof that a recognised claim is true.

    WHAT THIS CHECK DOES NOT DO:

      * The CLAIM side is a FIXED LIST of phrasings. Only the plural subjects,
        invariance wordings, class quantifiers and convention phrasings in the tuples
        below are recognised; a false claim worded outside them passes untouched.
      * It takes claims from THIS FILE'S MODULE DOCSTRING ONLY. Class docstrings are
        read only as derived FACT -- whether one exists, and whether it names a finding
        identifier. Method docstrings, comments and string data in method bodies are
        not read at all.
      * Scope is resolved by TEXT BLOCK, not by grammar: a blank-line paragraph, split
        again at each numbered population bullet. A claim is charged with every real
        class name in its block. A plural read attribution in a block naming fewer than
        two of this file's classes has nothing to compare against and passes untouched.
      * The census is SYNTACTIC and is KNOWN TO DIVERGE from what classes really read:
        it resolves no path literal, no from-imported production name, no alias and no
        helper. Seven classes read production harness source while holding a label SET
        that does not say so -- `TotalRendererUnitTests`, whose `read_text` call names
        no `__file__` and which therefore holds `NO_SOURCE`, and the six holding
        `OTHER_SOURCE` because they run `inspect.getsource` over an attribute of the
        from-imported `OrcaRuntimeHarness`: `RunBoundaryModelStateTests`,
        `ModelAwareGuardFailClosedTests`, `DocumentationAccuracyTests`,
        `SettledDispatchLoggingBoundaryTests`, `ModelAwarenessTruthValueTests` and
        `FreshSessionScopeTests`. Iteration 8 recorded these rather than repairing them.
      * `FINDING_ID_SHAPES` is a SHAPE match, not a lookup against a register of real
        findings, and it can err in both directions.
      * It does not touch the module-level hatch in `ClassDocstringClaimShapeTests`.
        That hatch resolves a `test_...` token appearing anywhere in the module
        docstring against the file-wide union of this file's test method names, and the
        module docstring names `test_no_unconditional_totality_claim_survives`, so the
        hatch is not what stops a module-level aggregate claim here.
    """

    #: The module whose source a Population 3 class may read instead of this file. The
    #: alias this file actually imports it under is derived, not assumed.
    PRODUCTION_MODULE_NAME = "orca_runtime_harness"

    #: Plural subjects that put ONE read attribution across a GROUP of classes. Every
    #: entry of the FOUR CLAIM-SIDE tuples -- this one, `PRODUCTION_INVARIANCE_SHAPES`,
    #: `CLASS_UNIVERSAL_SHAPES` and `FINDING_CONVENTION_SHAPES` -- carries its own
    #: specimen in `test_each_claim_side_shape_added_in_iteration_7_is_alive`, held as
    #: string data in a method body rather than as docstring prose.
    #: `FINDING_ID_SHAPES` is deliberately NOT in that set: it is a FACT-side matcher,
    #: and its limits are in the class docstring instead.
    JOINT_SUBJECT_SHAPES = (
        r"they",
        r"all\s+three",
        r"all\s+three\s+classes",
        r"the\s+three\s+classes",
        r"these\s+classes",
        r"both\s+classes",
        r"all\s+of\s+them",
    )

    #: Wordings that deny production source any reach over the subject.
    PRODUCTION_INVARIANCE_SHAPES = (
        r"\bproduction\b[^.;]{0,80}?\b(?:cannot|can\s+never|could\s+not|will\s+not|"
        r"would\s+not|does\s+not|do\s+not|never)\s+(?:\w+\s+){0,3}?"
        r"(?:move|reach|affect|touch|disturb)\b",
        r"\b(?:cannot|can\s+never|could\s+not|will\s+not|would\s+not|does\s+not|"
        r"do\s+not|never)\s+(?:\w+\s+){0,3}?(?:move|reach|affect|touch|disturb)\b"
        r"[^.;]{0,80}?\bproduction\b",
        r"\bno\s+production\b[^.;]{0,80}?\b(?:move|reach|affect|touch|disturb)\b",
        r"\binvariant\b[^.;]{0,60}?\bproduction\b",
        r"\bproduction\b[^.;]{0,60}?\binvariant\b",
    )

    #: Class-scoped universal quantifiers. These are the same four shapes iteration 7
    #: adds to `ClassDocstringClaimShapeTests.FORBIDDEN_CLAIM_SHAPES`, reused here
    #: against the module docstring where that class's hatch makes them inert.
    CLASS_UNIVERSAL_SHAPES = (
        r"\bevery\s+class(?:es)?\b",
        r"\beach\s+(?:of\s+(?:the|its|these)\s+)?class(?:es)?\b",
        r"\ball\s+(?:of\s+(?:the|its|these)\s+)?classes\b",
        r"\bboth\s+(?:of\s+(?:the|its|these)\s+)?classes\b",
    )

    #: The drafting convention a class-scoped universal must not be applied to while the
    #: derived census still holds a counterexample.
    FINDING_CONVENTION_SHAPES = (
        r"\bnames?\s+(?:the|a)\s+finding\b",
        r"\bnaming\s+(?:the|a)\s+finding\b",
        r"\bstates?\s+the\s+pre-fix\s+failure\s+mode\b",
    )

    #: The finding-identifier shapes this PR's reviews actually used: F-001..F-007,
    #: B1/B3/N1/N2, R1/R2, R20, N-1a/N-1b/N-2/N-3/N-4, M1..M7. Each listed form is
    #: matched by one of the two regexes below; what they are NOT is a register of real
    #: findings, and the class docstring states which way that cuts.
    FINDING_ID_SHAPES = (
        r"\b[FBNRM]-\d+[a-z]?\b",
        r"\b[BNRMF]\d+[a-z]?\b",
    )

    #: Anti-vacuity floor on the DERIVATION, not on any answer it produces. A derivation
    #: that reached no classes, or that recognised no read-subject call shapes, would
    #: satisfy every comparison below for the F-002 reason: nothing derived, so nothing
    #: can contradict. Thirty-five top-level classes exist as of iteration 7.
    MINIMUM_DERIVED_CLASSES = 30

    #: The classes F-006 is about. They must be IN the derivation or it is not deriving
    #: what the rule claims to cover.
    REQUIRED_IN_DERIVATION = (
        "TotalityClaimAccuracyTests",
        "RendererHelperBehaviourTests",
        "ClassDocstringClaimShapeTests",
    )

    @classmethod
    def _source_tree(cls):
        """Parse THIS file from disk, the surface `ClassDocstringClaimShapeTests` reads."""
        return ast.parse(Path(__file__).read_text(encoding="utf-8"))

    @classmethod
    def _production_aliases(cls, tree) -> set[str]:
        """Derive the local names this file binds the production harness module to."""
        aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if cls.PRODUCTION_MODULE_NAME in alias.name:
                        aliases.add(alias.asname or alias.name.split(".")[-1])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == cls.PRODUCTION_MODULE_NAME:
                        aliases.add(alias.asname or alias.name)
        return aliases

    @staticmethod
    def _called_name(call: ast.Call):
        """The attribute or bare name being called, or None."""
        func = call.func
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
        return None

    @classmethod
    def _read_subject(cls, node: ast.ClassDef, aliases: set[str]):
        """Derive (subject labels, one evidence snippet per label) for one class.

        Labels are `PRODUCTION_SOURCE`, `OWN_CLASS_SOURCE`, `THIS_FILE_SOURCE`,
        `OTHER_SOURCE` and `NO_SOURCE`, and they report which MODELLED CALL SHAPES occur
        in the class's own AST -- so the answer follows the code rather than any sentence
        about the code, within the model. Which real reads this model misses, and the
        seven live classes it currently misses them on, are in the class docstring's
        second limit; this helper is where that limit comes from.
        """
        labels: set[str] = set()
        evidence: dict[str, str] = {}
        references_production = any(
            isinstance(child, ast.Name) and child.id in aliases
            for child in ast.walk(node)
        )
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = cls._called_name(child)
            if name == "getsource":
                argument = ast.unparse(child.args[0]) if child.args else ""
                if argument.startswith("type("):
                    label = "OWN_CLASS_SOURCE"
                elif references_production:
                    label = "PRODUCTION_SOURCE"
                else:
                    label = "OTHER_SOURCE"
            elif name == "read_text" and "__file__" in ast.unparse(child):
                label = "THIS_FILE_SOURCE"
            else:
                continue
            labels.add(label)
            evidence.setdefault(label, ast.unparse(child))
        if not labels:
            labels.add("NO_SOURCE")
        return frozenset(labels), evidence

    @classmethod
    def _derivation(cls):
        """Derive {name: (labels, evidence, docstring)} per TOP-LEVEL class of THIS file.

        Nested classes are not walked as subjects of their own; a nested class's calls
        are attributed to the top-level class that encloses it.
        """
        tree = cls._source_tree()
        aliases = cls._production_aliases(tree)
        derived = {}
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            labels, evidence = cls._read_subject(node, aliases)
            derived[node.name] = (labels, evidence, ast.get_docstring(node) or "")
        return derived

    @classmethod
    def _module_blocks(cls):
        """Split the MODULE docstring into claim-scope blocks.

        A block is a blank-line paragraph, split again at every line that opens a
        numbered bullet (up to four leading spaces, digits, a dot, a space) -- which in
        this docstring is the population list, and is why a claim in bullet 3 is not
        charged with bullet 1's classes. The split is positional, so any other numbered
        list added to the module docstring would be split the same way.
        """
        doc = ast.get_docstring(cls._source_tree()) or ""
        blocks: list[str] = []
        for paragraph in re.split(r"\n\s*\n", doc):
            pieces = re.split(r"(?m)^\s{0,4}\d+\.\s", paragraph)
            blocks.extend(piece for piece in pieces if piece.strip())
        return blocks

    @classmethod
    def _classes_named_in(cls, block: str, derived) -> list[str]:
        """The backticked tokens of a block that name a TOP-LEVEL class in `derived`.

        A backticked token that is not a top-level class of this file -- an imported
        name, a method, a constant -- is not a class name here and is skipped.
        """
        seen: list[str] = []
        for token in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", block):
            if token in derived and token not in seen:
                seen.append(token)
        return seen

    def test_the_derivation_reaches_the_file_and_the_call_shapes_it_models(self) -> None:
        """Anti-vacuity first, on the derivation rather than on any of its answers.

        Every comparison in this class is `claim versus derived fact`, so a derivation
        that reached no classes, or that recognised none of the read-subject call
        shapes it models, would make all three comparisons pass for the exact F-002
        reason: nothing derived, nothing contradicted. This pins the derivation's reach
        before the comparisons are trusted. It deliberately also fails if the census
        stops DERIVING `PRODUCTION_SOURCE`, `THIS_FILE_SOURCE` or `NO_SOURCE` anywhere in
        this file, because at that point the comparisons below would be answering a
        question nobody is asking. It pins derived labels, not real reads: the class
        docstring's second limit is where those two are recorded as diverging.
        """
        derived = self._derivation()
        self.assertGreaterEqual(
            len(derived),
            self.MINIMUM_DERIVED_CLASSES,
            f"the derivation reached only {len(derived)} top-level classes; the AST "
            "scan has stopped reaching this file and every comparison in this class "
            "would pass vacuously",
        )
        for required in self.REQUIRED_IN_DERIVATION:
            with self.subTest(derived_class=required):
                self.assertIn(required, derived)
        observed = set()
        for labels, _evidence, _doc in derived.values():
            observed |= set(labels)
        for shape in ("PRODUCTION_SOURCE", "THIS_FILE_SOURCE", "NO_SOURCE"):
            with self.subTest(read_subject=shape):
                self.assertIn(
                    shape,
                    observed,
                    f"no class in this file derives {shape}; the call-shape model has "
                    "stopped recognising a read subject it is supposed to model, so a "
                    "false claim about that subject could no longer be contradicted",
                )

    def test_each_claim_side_shape_added_in_iteration_7_is_alive(self) -> None:
        """Each claim-side shape is a guard only if it matches prose it is meant to catch.

        The specimens are the real pre-F-005 sentences and their nearest siblings,
        carried as STRING DATA in a method body rather than as docstring prose -- the
        same convention `test_the_shape_vocabulary_catches_the_sentences_that_actually_failed`
        uses, and for the same reason: a docstring here holding one of them would be the
        defect. Unlike that test, this one pins EACH shape individually, because an
        existential check over a tuple cannot tell a live entry from a dead one.
        """
        joint_specimens = {
            r"they": "They read this file's own source text.",
            r"all\s+three": "all three read this file's own source",
            r"all\s+three\s+classes": "All three classes read the same source text",
            r"the\s+three\s+classes": "the three classes read their own source",
            r"these\s+classes": "these classes read this file's source text",
            r"both\s+classes": "both classes read the production harness source",
            r"all\s+of\s+them": "all of them read this file's own source text",
        }
        self.assertEqual(
            sorted(joint_specimens),
            sorted(self.JOINT_SUBJECT_SHAPES),
            "a joint-subject shape has no specimen, or a specimen has no shape; an "
            "entry with no specimen is an unproven entry",
        )
        for shape, specimen in joint_specimens.items():
            with self.subTest(joint_subject_shape=shape):
                self.assertTrue(
                    re.search(
                        rf"\b(?:{shape})\b[^.;]{{0,80}}?\bread(?:s|ing)?\b",
                        specimen,
                        re.IGNORECASE,
                    ),
                    f"{shape!r} no longer matches {specimen!r}; a claim shape that "
                    "matches nothing is a dead entry pretending to be a guard",
                )
        invariance_specimens = {
            self.PRODUCTION_INVARIANCE_SHAPES[0]:
                "Reverting a production line cannot move them, and that is intended.",
            self.PRODUCTION_INVARIANCE_SHAPES[1]:
                "they cannot move when production changes",
            self.PRODUCTION_INVARIANCE_SHAPES[2]:
                "no production edit can move them",
            self.PRODUCTION_INVARIANCE_SHAPES[3]:
                "they are invariant under production change",
            self.PRODUCTION_INVARIANCE_SHAPES[4]:
                "a production revert leaves them invariant",
        }
        universal_specimens = {
            self.CLASS_UNIVERSAL_SHAPES[0]: "Every class names the finding it locks.",
            self.CLASS_UNIVERSAL_SHAPES[1]:
                "Each class names the finding it locks and states the pre-fix failure "
                "mode.",
            self.CLASS_UNIVERSAL_SHAPES[2]:
                "All of the classes name the finding they lock.",
            self.CLASS_UNIVERSAL_SHAPES[3]:
                "Both classes name the finding they lock.",
        }
        convention_specimens = {
            self.FINDING_CONVENTION_SHAPES[0]: "it names the finding it locks",
            self.FINDING_CONVENTION_SHAPES[1]: "the convention of naming the finding",
            self.FINDING_CONVENTION_SHAPES[2]: "it states the pre-fix failure mode",
        }
        for tuple_name, shapes, specimens in (
            ("PRODUCTION_INVARIANCE_SHAPES",
             self.PRODUCTION_INVARIANCE_SHAPES, invariance_specimens),
            ("CLASS_UNIVERSAL_SHAPES",
             self.CLASS_UNIVERSAL_SHAPES, universal_specimens),
            ("FINDING_CONVENTION_SHAPES",
             self.FINDING_CONVENTION_SHAPES, convention_specimens),
        ):
            with self.subTest(shape_tuple=tuple_name):
                self.assertEqual(
                    sorted(specimens),
                    sorted(shapes),
                    f"an entry of {tuple_name} has no specimen, or a specimen has no "
                    "entry; an entry with no specimen is an unproven entry",
                )
            for shape, specimen in specimens.items():
                with self.subTest(shape_tuple=tuple_name, shape=shape):
                    self.assertTrue(
                        re.search(shape, specimen, re.IGNORECASE),
                        f"{tuple_name} entry {shape!r} no longer matches its specimen "
                        f"{specimen!r}; a claim shape that matches nothing is a dead "
                        "entry pretending to be a guard",
                    )

    def test_no_joint_read_subject_claim_survives_the_module_docstring(self) -> None:
        """F-006 falsehood 1: one shared read subject over classes that differ.

        The pre-fix sentence put one plural read attribution across the three Population
        3 classes, whose derived label SETS are not equal. This assertion compares those
        label SETS for WHOLE-SET EQUALITY and reads nothing else -- not the subject the
        sentence asserts. See the class docstring's `WHAT THE JOINT COMPARISON IS NOT`
        for the two directions in which that comparison and the claim's truth come
        apart.
        """
        derived = self._derivation()
        offenders: list[tuple[str, str]] = []
        for block in self._module_blocks():
            for shape in self.JOINT_SUBJECT_SHAPES:
                match = re.search(
                    rf"\b(?:{shape})\b[^.;]{{0,80}}?\bread(?:s|ing)?\b",
                    block,
                    re.IGNORECASE,
                )
                if match is None:
                    continue
                covered = self._classes_named_in(block, derived)
                subjects = {name: derived[name][0] for name in covered}
                with self.subTest(joint_claim=match.group(0)):
                    if len(set(subjects.values())) > 1:
                        readable = {
                            name: sorted(labels) for name, labels in subjects.items()
                        }
                        offenders.append((match.group(0), str(readable)))
                        self.fail(
                            f"the MODULE docstring attributes one read subject "
                            f"({match.group(0)!r}) to classes whose DERIVED subjects "
                            f"differ: {readable}. That is WHOLE-SET inequality of the "
                            "derived label sets and nothing more -- unequal sets can "
                            "still overlap on a genuinely shared subject, so this is "
                            "not evidence that the sentence is false of any of them "
                            "(review F-008). Name each class's subject separately, as "
                            "iteration 6 (review F-005) did -- do NOT change what a "
                            "class reads to make the sentence true (review F-006)"
                        )
        self.assertEqual(offenders, [])

    def test_no_production_invariance_claim_survives_the_module_docstring(self) -> None:
        """F-006 falsehood 2: production declared unable to move a production reader.

        The pre-fix sentence said reverting a production line could not move the
        Population 3 classes. `TotalityClaimAccuracyTests` does read production source:
        a banned claim re-inserted at any of its eight `INVENTORY` sites moves it, as
        the eight-site lock is designed to. This assertion reads the derived label SET
        rather than that reading, and fails only on `PRODUCTION_SOURCE` being a MEMBER
        of it.
        """
        derived = self._derivation()
        offenders: list[tuple[str, str]] = []
        for block in self._module_blocks():
            for shape in self.PRODUCTION_INVARIANCE_SHAPES:
                match = re.search(shape, block, re.IGNORECASE)
                if match is None:
                    continue
                covered = self._classes_named_in(block, derived)
                readers = [
                    name for name in covered
                    if "PRODUCTION_SOURCE" in derived[name][0]
                ]
                with self.subTest(invariance_claim=match.group(0)):
                    if readers:
                        evidence = {
                            name: derived[name][1].get("PRODUCTION_SOURCE")
                            for name in readers
                        }
                        offenders.append((match.group(0), str(readers)))
                        self.fail(
                            f"the MODULE docstring denies production any reach "
                            f"({match.group(0)!r}) over {readers}, which the derivation "
                            f"says read production harness source: {evidence}. State "
                            "the true sensitivity instead and name the assertion that "
                            "fires -- do NOT stop reading production source to make the "
                            "sentence true (review F-006)"
                        )
        self.assertEqual(offenders, [])

    def test_no_unsupported_class_universal_survives_the_module_docstring(self) -> None:
        """F-006 falsehood 3: the finding-naming convention quantified over classes.

        The pre-fix sentence said the convention held of each class. The derived census
        contradicts it twice over: some top-level classes carry no docstring at all, and
        some that do name no finding identifier. The verdict is that census, so the
        universal becomes permissible the moment the census stops holding a
        counterexample -- and `ClassDocstringClaimShapeTests`' module-level hatch cannot
        make this pass, because this comparison never consults it.
        """
        derived = self._derivation()
        undocumented = sorted(
            name for name, (_labels, _evidence, doc) in derived.items()
            if not doc.strip()
        )
        unattributed = sorted(
            name for name, (_labels, _evidence, doc) in derived.items()
            if doc.strip()
            and not any(
                re.search(shape, doc) for shape in self.FINDING_ID_SHAPES
            )
        )
        counterexamples = undocumented + unattributed
        offenders: list[str] = []
        for block in self._module_blocks():
            for quantifier in self.CLASS_UNIVERSAL_SHAPES:
                for convention in self.FINDING_CONVENTION_SHAPES:
                    for pattern in (
                        rf"(?:{quantifier})[^.;]{{0,120}}?(?:{convention})",
                        rf"(?:{convention})[^.;]{{0,120}}?(?:{quantifier})",
                    ):
                        match = re.search(pattern, block, re.IGNORECASE)
                        if match is None:
                            continue
                        with self.subTest(class_universal=match.group(0)[:60]):
                            if counterexamples:
                                offenders.append(match.group(0)[:60])
                                self.fail(
                                    "the MODULE docstring quantifies the "
                                    "finding-naming convention over classes "
                                    f"({match.group(0)[:60]!r}) while the derived "
                                    "census still holds counterexamples: "
                                    f"{undocumented} carry no docstring and "
                                    f"{unattributed} name no finding identifier. State "
                                    "it conditionally and name its exceptions, as "
                                    "iteration 6 (review F-005) did -- do NOT add "
                                    "docstrings merely to make the sentence true "
                                    "(review F-006)"
                                )
        self.assertEqual(offenders, [])

if __name__ == "__main__":
    unittest.main()
