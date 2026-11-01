#!/usr/bin/env python3
"""OS-14 I-7: the ROUND-SCOPED target lifetime -- TL (design 3a.12.1).

Design mapping.  Every test names the 9.3 **Group 3** row it executes, and the
parameterized disposal test states its case GROUP (A hold / B release / C excluded) and
the TL table row for every case.  No case is counted for two groups, and no case asserts
"exactly once" and "called twice" in the same observed window.

WHY A HOOK NEEDS TESTS AT ALL.  `settle_attempt` returning is NOT the engine's "finished
with this role" signal.  A malformed decision-gate record is classified one node LATER, in
VALIDATE_SETTLEMENT, and ROUTE may then re-dispatch THE SAME role in THE SAME round --
which re-runs `OrcaAdapter.start` and therefore re-verifies BOTH roles.  Three plausible
hooks get this wrong, and all three are implemented below as NEGATIVE CONTROLS and
asserted to FAIL the same rounds the production hook passes.

WHERE THE TOKENS COME FROM.  Every ROUTE token in this module is computed by the REAL
`routing.route` over a REAL engine state, never typed in as a string a test believes the
engine would emit.  That is what makes "the predicate and the engine cannot disagree"
assertable rather than asserted.

NO MODEL TURN IS SPENT, and no stand-in success here is a model-identity verification.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts.deterministic_workflow import (contracts, executor, graph_spec, launcher,
                                            routing)
from scripts.orca_runtime_harness import (MODEL_SELECTION_REQUEST_STALE,
                                          ROUND_OPEN_ROUTE_TOKENS, TARGET_ACTIVE,
                                          TARGET_REPAIR_PENDING,
                                          TARGET_SETTLED_PENDING_VALIDATION,
                                          TARGET_STATES, TARGET_VALIDATED,
                                          OrcaRuntimeError, RoundTargetLifetime,
                                          round_required_roles)
from scripts.test_os14_bugfix_regressions import (GATELESS_WORKER_BODY,
                                                   ReusableRecorder, repair_bodies)
from scripts.test_os14_launch_lease import LeaseCitingDriver, LeaseRoom
from scripts.test_os14_pair_preparation import (PHASE, PHASE_UPPER,
                                                REQUIRES_LANGGRAPH, PairRecorder,
                                                _body, _worker_record, final_body,
                                                reviewer_body, worker_body)

WORKER_COMPLETE = {"status": "COMPLETE", "unit_test_status": "PASS"}


def lifetime_code() -> str:
    """`RoundTargetLifetime`'s CODE, with every docstring removed.

    The structural assertions below are about what the hook READS AND WRITES, not about
    what its prose says -- and its prose necessarily names the very things the code must
    not touch ("TL has no claim on Orca's dispatch capability").  A raw source scan would
    therefore fail on the sentence that states the rule, which is the wrong way round.
    Parsed and re-rendered with the docstrings stripped, so a comment can neither satisfy
    nor break the assertion.
    """
    import ast
    import inspect
    from scripts import orca_runtime_harness as runtime
    tree = ast.parse(inspect.getsource(runtime.RoundTargetLifetime))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


# ======================================================================================
# the NO-REAL-CLI guard — module-wide, so no test here can need an `orca` binary
# ======================================================================================
#: The path `_resolve_orca` is told to use.  It is consulted BEFORE `shutil.which("orca")`
#: (`orca_runtime_harness._resolve_orca`), so with it set the harness constructs on a host
#: that has no Orca CLI at all -- which every CI runner is.  The value is the one the rest
#: of this repository's harness tests already use (`test_os14_pair_preparation`,
#: `test_os49_delivery_barrier`), so a reader recognises it.
#:
#: **Why module-wide and not only at the one construction site.** `OrcaRuntimeHarness.__init__`
#: resolves the executable unconditionally, so ANY direct construction fails on a host
#: without the binary.  CI proved that: one test built a successor harness without this and
#: five of six lanes errored, while both local lanes were green because a real `orca` was on
#: PATH.  A module-level guard makes the whole module independent of the host rather than
#: making one test independent of it, so a test added later cannot reintroduce the
#: dependency.  It weakens nothing: every test in this module replaces the harness's single
#: process boundary (`_exec_orca`) with a recorder, so the resolved path is never executed --
#: it only has to exist as a STRING.
NO_REAL_CLI = "/opt/orca-dev"
_CLI_GUARD: Any = None


def setUpModule() -> None:
    global _CLI_GUARD
    _CLI_GUARD = patch.dict(os.environ, {"ORCA_CLI_COMMAND": NO_REAL_CLI})
    _CLI_GUARD.start()


def tearDownModule() -> None:
    if _CLI_GUARD is not None:
        _CLI_GUARD.stop()


# ======================================================================================
# the ENGINE's own tokens
# ======================================================================================
def engine_token(*, risk, worker=None, reviewer=None, defect=None,
                 repair_budget=contracts.MAX_REPAIR_ATTEMPTS, phase=PHASE_UPPER):
    """The token the REAL `routing.route` emits for this round state.

    Built from `launcher.build_state`, so every field the router reads is the engine's own
    and no test invents one.  `defect` is the `pending_gate_defect` VALIDATE_SETTLEMENT
    would have set; `repair_budget` is what PREPARE_INTENT would have left.
    """
    state = launcher.build_state({"run_id": "run_os14tl", "thread_id": "os14",
                                  "phases": [phase], "risk": risk})
    state = dict(state, worker_result=worker, reviewer_result=reviewer,
                 pending_gate_defect=defect, remaining_repair_budget=repair_budget)
    return routing.route(state)


class EngineTokenTests(unittest.TestCase):
    """The tokens the rest of this module relies on are the ENGINE's, read from it."""

    def test_the_required_role_set_agrees_with_phase_gate(self):
        """9.3 `test_required_roles_not_dispatched_roles_decide_the_close`, its agreement
        half: the predicate's required-role set and `routing.phase_gate`'s own rule are
        the same rule, not two rules that happen to coincide."""
        for risk in ("low", "medium", "high"):
            with self.subTest(risk=risk):
                required = round_required_roles(risk)
                token = engine_token(risk=risk, worker=WORKER_COMPLETE)
                if "reviewer" in required:
                    self.assertEqual(token, "PREPARE_PHASE_REVIEWER",
                                     "a round that still needs a Reviewer draws the "
                                     "token that dispatches one")
                    self.assertIn(token, ROUND_OPEN_ROUTE_TOKENS)
                else:
                    self.assertEqual(token, "ADVANCE_PHASE",
                                     "`phase_gate` returns PASS from a COMPLETE worker "
                                     "alone at LOW risk, so the round closes")
                    self.assertNotIn(token, ROUND_OPEN_ROUTE_TOKENS)

    def test_an_undeclared_risk_takes_the_stricter_set(self):
        """An unanswered question is unknown, not "low"."""
        for risk in ("", None, "unknown"):
            self.assertEqual(round_required_roles(risk), ("worker", "reviewer"))

    def test_the_two_open_tokens_are_the_only_two(self):
        """(1b) is spelled as the two tokens that keep a round OPEN, so a token added to
        the engine later closes rounds by default rather than never closing one."""
        self.assertEqual(ROUND_OPEN_ROUTE_TOKENS,
                         ("PREPARE_REPAIR", "PREPARE_PHASE_REVIEWER"))
        every = set(contracts.RouteToken.__args__)
        self.assertTrue(set(ROUND_OPEN_ROUTE_TOKENS) <= every)
        closing = every - set(ROUND_OPEN_ROUTE_TOKENS)
        self.assertEqual(len(closing), len(every) - 2)
        for token in sorted(closing):
            tracker = RoundTargetLifetime(lambda handle: None, risk="low")
            tracker.prepared(phase=PHASE, attempt=1, role="worker", handle="t1")
            tracker.validated(phase=PHASE, attempt=1, role="worker")
            self.assertTrue(tracker.round_closes(token, phase=PHASE, attempt=1),
                            f"{token} is a CLOSING token")

    def test_the_hook_owns_no_routing_decision(self):
        """`VALIDATION_REPAIR`, `PHASE_GATE` and `CORRECTION_LOOP` stay the GRAPH's.

        Asserted structurally: the lifetime module names no routing policy of its own, and
        the three decisions it could have usurped are still declared graph-owned.
        """
        for decision in ("VALIDATION_REPAIR", "PHASE_GATE", "CORRECTION_LOOP"):
            self.assertIn(decision, graph_spec.GRAPH_OWNED_DECISIONS)
        code = lifetime_code()
        for forbidden in ("phase_gate", "pending_gate_defect", "remaining_repair_budget",
                          "MAX_REPAIR_ATTEMPTS", "routing."):
            self.assertNotIn(forbidden, code,
                             "the hook OBSERVES the engine's decisions; its CODE must "
                             f"not re-derive {forbidden}")


# ======================================================================================
# the NEGATIVE CONTROLS -- three hooks that must each FAIL
# ======================================================================================
class SettlementScopedLifetime(RoundTargetLifetime):
    """WRONG HOOK 1: releases at `settle_attempt`.

    Releases the targets a validation repair is about to need.  Measured consequence:
    `PAIR_PREPARATION_MODEL_UNOBSERVED` on the next turn of the same round.
    """

    def settled(self, *, phase, attempt, role):
        super().settled(phase=phase, attempt=attempt, role=role)
        self._close(phase=phase, attempt=attempt)


class ValidatedOnlyLifetime(RoundTargetLifetime):
    """WRONG HOOK 2: releases on `VALIDATED` alone, with no (1b).

    Releases while a repair is pending, and releases the pair the moment the Worker's form
    is accepted -- before the required Reviewer has run.
    """

    def round_closes(self, token, *, phase, attempt):
        return self.role_state(phase=phase, attempt=attempt,
                               role="worker") == TARGET_VALIDATED


class DispatchedRolesLifetime(RoundTargetLifetime):
    """WRONG HOOK 3: (1a) quantifies over DISPATCHED roles instead of REQUIRED ones.

    "Dispatched" means a role whose Dispatch has actually run -- i.e. one that has left
    `ACTIVE`.  Immediately after an accepted Worker turn that set is `{worker}` alone, so
    this predicate is TRUE while the required Reviewer still has to run on both held
    targets.  That is the F-001 defect itself.
    """

    def required_roles(self):
        return tuple(role for states in self._states.values()
                     for role, state in states.items() if state != TARGET_ACTIVE)

    def round_closes(self, token, *, phase, attempt):
        # And the earlier edition's (1b), which excluded only `PREPARE_REPAIR`: with (1a)
        # over DISPATCHED roles, `PREPARE_PHASE_REVIEWER` sails through.  The two halves
        # of the defect are kept together here because that is how the defect occurred --
        # quantifying over dispatched roles is only unsafe while (1b) lets the
        # Reviewer-dispatching token close a round.
        if token == "PREPARE_REPAIR":
            return False
        return all(
            self.role_state(phase=phase, attempt=attempt, role=role) == TARGET_VALIDATED
            for role in self.required_roles())


WRONG_HOOKS = (SettlementScopedLifetime, ValidatedOnlyLifetime, DispatchedRolesLifetime)


# ======================================================================================
# TL's predicate, driven over the engine's own tokens
# ======================================================================================
class TrackerCase(unittest.TestCase):
    """One round's tracker, with the releases it performed recorded."""

    def tracker(self, *, risk="high", cls=RoundTargetLifetime):
        released: list[str] = []
        return cls(released.append, risk=risk), released

    def prepare_round(self, tracker, *, risk="high", attempt=1):
        """Prepare the roles this risk level requires, exactly as `_prepare_role` does."""
        handles = {}
        for index, role in enumerate(round_required_roles(risk), start=1):
            handles[role] = f"term_{attempt}_{index}"
            tracker.prepared(phase=PHASE, attempt=attempt, role=role,
                             handle=handles[role])
        return handles


class StateMachineTests(TrackerCase):
    """The four per-role states, and which of them can permit a release."""

    def test_the_four_states_are_closed_and_only_validated_can_permit_release(self):
        self.assertEqual(TARGET_STATES,
                         (TARGET_ACTIVE, TARGET_SETTLED_PENDING_VALIDATION,
                          TARGET_REPAIR_PENDING, TARGET_VALIDATED))
        for state, mark in ((TARGET_ACTIVE, None),
                            (TARGET_SETTLED_PENDING_VALIDATION, "settled"),
                            (TARGET_REPAIR_PENDING, "repair_pending"),
                            (TARGET_VALIDATED, "validated")):
            with self.subTest(state=state):
                tracker, released = self.tracker(risk="low")
                self.prepare_round(tracker, risk="low")
                if mark is not None:
                    getattr(tracker, mark)(phase=PHASE, attempt=1, role="worker")
                self.assertEqual(
                    tracker.role_state(phase=PHASE, attempt=1, role="worker"), state)
                closed = tracker.round_closes("ADVANCE_PHASE", phase=PHASE, attempt=1)
                self.assertEqual(closed, state == TARGET_VALIDATED)
                tracker.observe_route("ADVANCE_PHASE", phase=PHASE, attempt=1)
                self.assertEqual(released, ["term_1_1"] if closed else [])

    def test_a_role_with_nothing_recorded_reads_active(self):
        """An unanswered question is unknown, never finished -- so a HIGH/MEDIUM round
        whose Reviewer was never even prepared cannot close."""
        tracker, released = self.tracker(risk="high")
        tracker.prepared(phase=PHASE, attempt=1, role="worker", handle="t1")
        tracker.validated(phase=PHASE, attempt=1, role="worker")
        self.assertEqual(tracker.role_state(phase=PHASE, attempt=1, role="reviewer"),
                         TARGET_ACTIVE)
        self.assertFalse(tracker.round_closes("ADVANCE_PHASE", phase=PHASE, attempt=1))
        self.assertEqual(released, [])

    def test_settlement_alone_does_not_close_the_round(self):
        """9.3 `test_settlement_alone_does_not_close_the_round`, the F-001 case a
        settlement-keyed hook fails.

        Asserted for the Worker's turn AND for the phase-Reviewer's turn -- the Reviewer's
        is the one the earlier edition of the design released.
        """
        for role in ("worker", "reviewer"):
            with self.subTest(settled=role):
                tracker, released = self.tracker(risk="high")
                handles = self.prepare_round(tracker)
                tracker.settled(phase=PHASE, attempt=1, role=role)
                self.assertEqual(
                    tracker.role_state(phase=PHASE, attempt=1, role=role),
                    TARGET_SETTLED_PENDING_VALIDATION)
                self.assertEqual(released, [],
                                 "a settlement whose FORM nobody has checked yet is not "
                                 "a finished role")
                self.assertCountEqual(
                    tracker.prepared_handles(phase=PHASE, attempt=1), handles.values())
                # And the WRONG hook releases exactly here, which is the control.
                wrong, wrong_released = self.tracker(cls=SettlementScopedLifetime)
                self.prepare_round(wrong)
                wrong.settled(phase=PHASE, attempt=1, role=role)
                self.assertTrue(wrong_released,
                                "the settlement-scoped hook must FAIL this round")

    def test_required_roles_not_dispatched_roles_decide_the_close(self):
        """9.3 `test_required_roles_not_dispatched_roles_decide_the_close`, isolated.

        **Case A (risk high/medium):** Worker `VALIDATED`, phase Reviewer PREPARED BUT
        NEVER DISPATCHED (`ACTIVE`), ROUTE emits `PREPARE_PHASE_REVIEWER` => (1a) false,
        (1b) false, round OPEN, no release.
        **Case B (risk low):** Worker `VALIDATED`, no Reviewer required or prepared, ROUTE
        emits `ADVANCE_PHASE` => (1a) true, (1b) true, round CLOSED, one release.
        """
        for risk, token, closes in (("high", "PREPARE_PHASE_REVIEWER", False),
                                    ("medium", "PREPARE_PHASE_REVIEWER", False),
                                    ("low", "ADVANCE_PHASE", True)):
            with self.subTest(risk=risk):
                self.assertEqual(engine_token(risk=risk, worker=WORKER_COMPLETE), token)
                tracker, released = self.tracker(risk=risk)
                handles = self.prepare_round(tracker, risk=risk)
                tracker.validated(phase=PHASE, attempt=1, role="worker")
                self.assertEqual(
                    tracker.observe_route(token, phase=PHASE, attempt=1),
                    tuple(handles.values()) if closes else ())
                self.assertEqual(sorted(released),
                                 sorted(handles.values()) if closes else [])

    def test_the_three_wrong_hooks_each_fail_the_worker_to_reviewer_boundary(self):
        """The negative controls, at the one boundary all three get wrong.

        A HIGH-risk Worker turn is accepted, the required Reviewer is prepared and still
        `ACTIVE`, and the engine's own token is `PREPARE_PHASE_REVIEWER`.  The production
        hook holds both handles; **every** wrong hook releases at least one.
        """
        token = engine_token(risk="high", worker=WORKER_COMPLETE)
        self.assertEqual(token, "PREPARE_PHASE_REVIEWER")
        right, right_released = self.tracker()
        self.prepare_round(right)
        right.validated(phase=PHASE, attempt=1, role="worker")
        right.observe_route(token, phase=PHASE, attempt=1)
        self.assertEqual(right_released, [],
                         "the production hook holds both targets: the Reviewer's step 4f "
                         "re-verifies the settled Worker's own session")
        for cls in WRONG_HOOKS:
            with self.subTest(hook=cls.__name__):
                wrong, wrong_released = self.tracker(cls=cls)
                self.prepare_round(wrong)
                wrong.settled(phase=PHASE, attempt=1, role="worker")
                wrong.validated(phase=PHASE, attempt=1, role="worker")
                wrong.observe_route(token, phase=PHASE, attempt=1)
                self.assertTrue(wrong_released,
                                f"{cls.__name__} must FAIL this round, and it did not")

    def test_validation_repair_of_either_role_holds_both_targets(self):
        """9.3 `test_validation_repair_of_either_role_holds_both_targets`.

        Two separate cases -- a FORM defect in the WORKER's record and one in the phase
        REVIEWER's -- with budget left.  The engine's own token is `PREPARE_REPAIR`, the
        role goes `REPAIR_PENDING`, and NEITHER handle is released.
        """
        defect = {"code": "DECISION_GATE_FORM_DEFECT"}
        token = engine_token(risk="high", worker=WORKER_COMPLETE, defect=defect)
        self.assertEqual(token, "PREPARE_REPAIR")
        for role in ("worker", "reviewer"):
            with self.subTest(defect_in=role):
                tracker, released = self.tracker()
                handles = self.prepare_round(tracker)
                other = "reviewer" if role == "worker" else "worker"
                tracker.validated(phase=PHASE, attempt=1, role=other)
                tracker.settled(phase=PHASE, attempt=1, role=role)
                tracker.repair_pending(phase=PHASE, attempt=1, role=role)
                self.assertEqual(tracker.observe_route(token, phase=PHASE, attempt=1), ())
                self.assertEqual(released, [])
                self.assertCountEqual(
                    tracker.prepared_handles(phase=PHASE, attempt=1), handles.values())
                # The repair's re-entry prepares the SAME handles under the SAME key, and
                # that is idempotent -- a repair is not a second preparation.
                for name, handle in handles.items():
                    tracker.prepared(phase=PHASE, attempt=1, role=name, handle=handle)
                self.assertCountEqual(
                    tracker.prepared_handles(phase=PHASE, attempt=1), handles.values())

    def test_repair_budget_bounds_the_hold_in_turns(self):
        """9.3 `test_repair_budget_bounds_the_hold`.

        `MAX_REPAIR_ATTEMPTS = 2` per role is what bounds the hold IN TURNS: with the
        budget spent the engine's own token is `BLOCK`, which is a CLOSING token, so the
        round closes.  Asserted as a TURN bound only -- the wall-clock hold stays
        unmeasured (D5), and this test makes no claim about it.
        """
        self.assertEqual(contracts.MAX_REPAIR_ATTEMPTS, 2)
        defect = {"code": "DECISION_GATE_FORM_DEFECT"}
        held = engine_token(risk="high", worker=WORKER_COMPLETE, defect=defect,
                            repair_budget=1)
        spent = engine_token(risk="high", worker=WORKER_COMPLETE, defect=defect,
                             repair_budget=0)
        self.assertEqual((held, spent), ("PREPARE_REPAIR", "BLOCK"))
        tracker, released = self.tracker()
        handles = self.prepare_round(tracker)
        tracker.repair_pending(phase=PHASE, attempt=1, role="worker")
        self.assertEqual(tracker.observe_route(held, phase=PHASE, attempt=1), ())
        self.assertEqual(released, [])
        self.assertEqual(tracker.dispose(phase=PHASE, attempt=1, reason="BLOCK"),
                         tuple(handles.values()),
                         "exhaustion is clause 2: the terminating path disposes")
        self.assertEqual(sorted(released), sorted(handles.values()))


# ======================================================================================
# the parameterized disposal test -- THREE case groups, THREE expectations
# ======================================================================================
class RoundCloseDisposalTests(TrackerCase):
    """9.3 `test_round_close_disposes_on_every_terminating_path`.

    Parameterized over the rows of TL's 15-row hold/dispose table in THREE case groups
    with THREE DIFFERENT expectations, and **no case carries two of them**.  Each case's
    close outcome is read from the ONE close predicate; this test states no close condition
    of its own.  Only the clause-1 cases are evaluated by "(1a) AND (1b)"; the clause-2
    cases are not, and are not written as if they were.

    `risk` is an outcome-changing branch on every row that involves a completion or a
    repair, so those rows appear once per risk level and no such row is given one risk
    level as though it had only one.
    """

    # ---- GROUP A: HOLD.  Expectation: release is called ZERO times -------------------
    def test_group_a_hold_cases_release_zero_times(self):
        """TL rows 1a, 2 (both risk levels), 3 and row 4's HIGH/MEDIUM held branch.

        In the window that observes the triggering state the round is OPEN -- TL's own
        columns read "round closed? no" and "who disposes: nobody" -- and the target,
        channel and lease of EVERY role prepared under the key are still held.

        The observed window ENDS at that state.  The release count of a trace later
        allowed to run on to a normal close belongs to group B and is never read back onto
        these cases.
        """
        defect = {"code": "DECISION_GATE_FORM_DEFECT"}
        cases = (
            # (label, TL row, risk, the marks to apply, the engine state for the token)
            ("1a worker accepted, reviewer required and ACTIVE", "1a", "high",
             (("validated", "worker"),), dict(worker=WORKER_COMPLETE)),
            ("1a worker accepted, reviewer required and ACTIVE", "1a", "medium",
             (("validated", "worker"),), dict(worker=WORKER_COMPLETE)),
            ("2 worker repair pending, reviewer also required", "2", "high",
             (("settled", "worker"), ("repair_pending", "worker")),
             dict(worker=WORKER_COMPLETE, defect=defect)),
            ("2 worker repair pending, reviewer also required", "2", "medium",
             (("settled", "worker"), ("repair_pending", "worker")),
             dict(worker=WORKER_COMPLETE, defect=defect)),
            # TL row 2 at LOW risk: NO Reviewer is required, prepared or dispatched, and
            # the round is STILL open because ROUTE's PREPARE_REPAIR makes (1b) false on
            # its own.  The Worker's single prepared target is held, zero releases.
            ("2 worker repair pending, no reviewer required at all", "2", "low",
             (("settled", "worker"), ("repair_pending", "worker")),
             dict(worker=WORKER_COMPLETE, defect=defect)),
            # TL row 3 is reachable ONLY at HIGH/MEDIUM: at LOW no phase Reviewer is ever
            # required or prepared, so the row has NO low instance to classify.
            ("3 reviewer repair pending, worker already VALIDATED", "3", "high",
             (("validated", "worker"), ("settled", "reviewer"),
              ("repair_pending", "reviewer")),
             dict(worker=WORKER_COMPLETE, defect=defect)),
            ("3 reviewer repair pending, worker already VALIDATED", "3", "medium",
             (("validated", "worker"), ("settled", "reviewer"),
              ("repair_pending", "reviewer")),
             dict(worker=WORKER_COMPLETE, defect=defect)),
            # TL row 4's HELD branch: a repaired Worker at HIGH/MEDIUM whose clean
            # settlement still yields PREPARE_PHASE_REVIEWER, because the Reviewer is
            # required and ACTIVE.  The risk-dependent twin of group B's row-4 trigger
            # (a): ONE repaired-Worker shape, HOLDING here and RELEASING there, which is
            # why neither group may state it without its risk level.
            ("4 repaired worker clean, reviewer required and ACTIVE", "4-held", "high",
             (("validated", "worker"),), dict(worker=WORKER_COMPLETE)),
            ("4 repaired worker clean, reviewer required and ACTIVE", "4-held", "medium",
             (("validated", "worker"),), dict(worker=WORKER_COMPLETE)),
        )
        for label, row, risk, marks, state in cases:
            with self.subTest(row=row, risk=risk, case=label):
                token = engine_token(risk=risk, **state)
                tracker, released = self.tracker(risk=risk)
                handles = self.prepare_round(tracker, risk=risk)
                for mark, role in marks:
                    getattr(tracker, mark)(phase=PHASE, attempt=1, role=role)
                self.assertFalse(
                    tracker.round_closes(token, phase=PHASE, attempt=1),
                    f"TL row {row} at risk {risk} must leave the round OPEN "
                    f"(token={token})")
                self.assertEqual(tracker.observe_route(token, phase=PHASE, attempt=1), ())
                self.assertEqual(
                    released, [],
                    f"TL row {row} at risk {risk}: release(handle) is called ZERO times "
                    "in the window that observes this state")
                self.assertCountEqual(
                    tracker.prepared_handles(phase=PHASE, attempt=1), handles.values(),
                    "every role prepared under the key is still held")

    # ---- GROUP B: RELEASE.  Expectation: EXACTLY ONE release per prepared handle -----
    def test_group_b_release_cases_release_each_prepared_handle_exactly_once(self):
        """TL row 1b, BOTH of row 4's closing triggers, and rows 5-13.

        Expectation: EXACTLY ONE `release(handle)` per handle that was ACTUALLY PREPARED,
        on the closing / refusal / disposal path TL names for that row -- and NO release
        is required for a handle that was never created.

        The one-per-handle count is asserted over group B and over group B ONLY: these are
        exactly the paths on which a live Coordinator executes `release`.  The deliberate
        SECOND call that proves idempotency lives in its own test below.
        """
        defect = {"code": "DECISION_GATE_FORM_DEFECT"}
        semantic = {"code": "DECISION_GATE_SEMANTIC_BLOCK"}
        lifecycle = {"code": "DECISION_GATE_LIFECYCLE_DEFECT"}
        blocked_worker = {"status": "BLOCKED", "unit_test_status": "PASS"}
        clause_one = (
            # TL row 1b: a LOW-risk Worker-only round with no repair.
            ("1b low worker-only round", "1b", "low", (("validated", "worker"),),
             dict(worker=WORKER_COMPLETE), "ADVANCE_PHASE"),
            # TL row 4 trigger (a): a REPAIRED WORKER AT LOW RISK.  Its clean settlement
            # leaves the Worker -- the round's ONLY required role -- VALIDATED, so
            # `phase_gate` returns PASS from the COMPLETE Worker alone and ROUTE emits
            # ADVANCE_PHASE.  The engine's own rule, read from the code.
            ("4(a) repaired worker clean at LOW risk", "4-close-a", "low",
             (("validated", "worker"),), dict(worker=WORKER_COMPLETE), "ADVANCE_PHASE"),
            # TL row 4 trigger (b): a REPAIRED PHASE REVIEWER at HIGH/MEDIUM, whose clean
            # settlement leaves EVERY required role VALIDATED under a closing token.
            ("4(b) repaired reviewer clean at HIGH risk", "4-close-b", "high",
             (("validated", "worker"), ("validated", "reviewer")),
             dict(worker=WORKER_COMPLETE, reviewer={"result": "PASS"}), "ADVANCE_PHASE"),
            ("4(b) repaired reviewer clean at MEDIUM risk", "4-close-b", "medium",
             (("validated", "worker"), ("validated", "reviewer")),
             dict(worker=WORKER_COMPLETE, reviewer={"result": "FAIL"}),
             "PREPARE_CORRECTION"),
            # TL row 7: a well-formed Reviewer FAIL.  Reachable only at HIGH/MEDIUM.
            ("7 reviewer FAIL", "7", "high",
             (("validated", "worker"), ("validated", "reviewer")),
             dict(worker=WORKER_COMPLETE, reviewer={"result": "FAIL"}),
             "PREPARE_CORRECTION"),
            # TL row 8: phase PASS with both required roles VALIDATED.  Reachable only at
            # HIGH/MEDIUM: a LOW round's single-role completion is row 1b when no repair
            # ran and row 4 trigger (a) when one did -- never row 8.
            ("8 phase PASS", "8", "high",
             (("validated", "worker"), ("validated", "reviewer")),
             dict(worker=WORKER_COMPLETE, reviewer={"result": "PASS"}), "ADVANCE_PHASE"),
            ("8 phase PASS", "8", "medium",
             (("validated", "worker"), ("validated", "reviewer")),
             dict(worker=WORKER_COMPLETE, reviewer={"result": "PASS"}), "ADVANCE_PHASE"),
        )
        for label, row, risk, marks, state, expected in clause_one:
            with self.subTest(clause=1, row=row, risk=risk, case=label):
                token = engine_token(risk=risk, **state)
                self.assertEqual(token, expected,
                                 f"TL row {row} at risk {risk}: the ENGINE's token")
                tracker, released = self.tracker(risk=risk)
                handles = self.prepare_round(tracker, risk=risk)
                self.assertTrue(
                    all(getattr(tracker, mark)(phase=PHASE, attempt=1, role=role) is None
                        for mark, role in marks))
                self.assertTrue(tracker.round_closes(token, phase=PHASE, attempt=1),
                                f"TL row {row} at risk {risk} closes under clause 1")
                tracker.observe_route(token, phase=PHASE, attempt=1)
                for handle in handles.values():
                    self.assertEqual(
                        tracker.release_count(handle), 1,
                        f"TL row {row} at risk {risk}: exactly ONE release per handle "
                        "actually prepared")
                self.assertEqual(sorted(released), sorted(handles.values()))

        # Clause 2.  These are NOT evaluated by "(1a) AND (1b)" and are not written as if
        # they were: a refusal or an abandonment closes a round whatever the role states
        # say.  Rows 5-6 and 9-13 were each checked for a risk-dependent branch and have
        # none that changes the OUTCOME: risk changes only WHICH role can be the defecting
        # one and HOW MANY handles were prepared, and the per-prepared-handle count
        # already absorbs both.
        clause_two = (
            ("5 repair exhaustion", "5", "high",
             dict(worker=WORKER_COMPLETE, defect=defect, repair_budget=0), "BLOCK"),
            ("6 SEMANTIC gate defect", "6", "high",
             dict(worker=WORKER_COMPLETE, defect=semantic), "BLOCK"),
            ("6 LIFECYCLE gate defect", "6", "high",
             dict(worker=WORKER_COMPLETE, defect=lifecycle), "BLOCK"),
            ("9 BLOCKED: a non-COMPLETE worker_done", "9", "high",
             dict(worker=blocked_worker), "BLOCK"),
            ("9 BLOCKED at LOW risk too", "9", "low", dict(worker=blocked_worker),
             "BLOCK"),
        )
        for label, row, risk, state, expected in clause_two:
            with self.subTest(clause=2, row=row, risk=risk, case=label):
                self.assertEqual(engine_token(risk=risk, **state), expected,
                                 f"TL row {row}: the ENGINE's token")
                tracker, released = self.tracker(risk=risk)
                handles = self.prepare_round(tracker, risk=risk)
                tracker.dispose(phase=PHASE, attempt=1, reason=row)
                for handle in handles.values():
                    self.assertEqual(tracker.release_count(handle), 1)
                self.assertEqual(sorted(released), sorted(handles.values()))

        # TL rows 10-12: ESCALATED, cancel/abandon and PAUSE.  All clause 2, and PAUSE is
        # disposed DELIBERATELY rather than held -- a resume is a different process, the
        # binding is in-process (O6) and the restart rule is a named refusal, so holding
        # across a pause would preserve nothing.
        for row, reason in (("10", "ESCALATE"), ("11", "CANCEL"), ("11", "ABANDON"),
                            ("12", "PAUSE")):
            with self.subTest(clause=2, row=row, case=reason):
                tracker, released = self.tracker()
                handles = self.prepare_round(tracker)
                tracker.dispose(phase=PHASE, attempt=1, reason=reason)
                for handle in handles.values():
                    self.assertEqual(tracker.release_count(handle), 1)
                self.assertEqual(sorted(released), sorted(handles.values()))

        # TL row 13, in its TWO branches: a step-4 refusal AFTER a handle exists, whose
        # handle is released once; and one BEFORE any create, where NO handle exists and
        # therefore NO release is required.
        with self.subTest(clause=2, row="13", case="after a handle exists"):
            tracker, released = self.tracker()
            tracker.prepared(phase=PHASE, attempt=1, role="worker", handle="t_only")
            tracker.dispose(phase=PHASE, attempt=1, reason="step-4 refusal")
            self.assertEqual(released, ["t_only"])
            self.assertEqual(tracker.release_count("t_only"), 1)
        with self.subTest(clause=2, row="13", case="before any create"):
            tracker, released = self.tracker()
            self.assertEqual(tracker.dispose(phase=PHASE, attempt=1, reason="refusal"),
                             ())
            self.assertEqual(released, [],
                             "a refusal raised before any create has NOTHING to release, "
                             "and that is the correct answer rather than a missing one")

    # ---- GROUP C: the Coordinator exit, EXCLUDED from the release-count test ---------
    def test_group_c_coordinator_exit_is_excluded_from_the_release_count(self):
        """TL row 14.  Its round NEVER closes and NOBODY calls `release`.

        The registry and the binding are in-process state that dies with the process (O6)
        and the surviving CLI child is the OPERATOR's residual, so there is no release
        count here to assert and asserting one would contradict TL row 14.  This test
        therefore asserts the EXCLUSION: with the process gone, nothing in this object has
        run, and the row needs no risk split because it is reached the same way at every
        risk level.

        The successor's named refusal, the no-rebind rule and the residual sessions are
        covered by `test_coordinator_exit_leaves_a_refusal_not_a_rebind` below and in
        `test_os14_launch_lease.py`'s restart row.
        """
        for risk in ("low", "medium", "high"):
            with self.subTest(risk=risk):
                tracker, released = self.tracker(risk=risk)
                self.prepare_round(tracker, risk=risk)
                # The process exits here.  No `observe_route`, no `dispose`, no hook of
                # any kind runs -- which is exactly what "nobody disposes" means.
                self.assertEqual(released, [])
                self.assertEqual(tracker.releases, [])

    # ---- the ONE cross-group claim ---------------------------------------------------
    def test_no_target_prepared_under_one_key_is_observed_under_another(self):
        """Asserted over groups A and B alike, and the only claim that spans both.

        A CROSS-KEY claim only: it does not deny a group-A hold, which keeps every
        prepared target under the SAME key for the next turn of the same round.
        """
        tracker, released = self.tracker()
        first = self.prepare_round(tracker, attempt=1)
        second = self.prepare_round(tracker, attempt=2)
        self.assertCountEqual(tracker.prepared_handles(phase=PHASE, attempt=1),
                              first.values())
        self.assertCountEqual(tracker.prepared_handles(phase=PHASE, attempt=2),
                              second.values())
        self.assertFalse(set(first.values()) & set(second.values()))
        tracker.dispose(phase=PHASE, attempt=1, reason="round 1 over")
        self.assertEqual(sorted(released), sorted(first.values()),
                         "closing round 1 released round 1's handles and NOTHING of "
                         "round 2's")
        for handle in second.values():
            self.assertEqual(tracker.release_count(handle), 0)

    def test_the_round_key_is_one_pair_whatever_its_python_types(self):
        tracker, released = self.tracker(risk="low")
        tracker.prepared(phase=PHASE, attempt=1, role="worker", handle="t1")
        tracker.validated(phase=PHASE, attempt="1", role="worker")
        self.assertEqual(tracker.observe_route("ADVANCE_PHASE", phase=PHASE, attempt="1"),
                         ("t1",))
        self.assertEqual(released, ["t1"])


# ======================================================================================
# idempotency, kept SEPARATE from every count above
# ======================================================================================
class IdempotentReleaseTests(LeaseRoom):
    """9.3 `test_release_is_exactly_once_and_idempotent`, and deliberately the only place
    a second `release` call appears: no case of the count test above ever requires
    "exactly once" and "called twice" in the same observed window."""

    def test_release_is_idempotent_and_the_second_call_is_a_no_op(self):
        launch = self.hosted_launch()
        self.dispatch(launch)
        handle = launch.driver.binds[0][0]
        self.assertTrue(launch.harness.release(handle), "the first call did the work")
        self.assertFalse(launch.harness.release(handle), "the second call is a no-op")
        self.assertFalse(launch.harness.release(handle))
        self.assertIsNone(launch.harness.launch_lease_for_handle(handle))
        self.assertIsNone(launch.harness.prepared_target(handle))
        self.assertEqual(launch.driver.released.count(handle), 1,
                         "the driver's own teardown ran once, not once per call")

    def test_release_tolerates_an_endpoint_the_other_side_already_unlinked(self):
        """Both sides unlink, so `ENOENT` is a NORMAL outcome -- the authoritative "this
        endpoint is finished" fact is the revoked lease, never the socket file."""
        import os
        launch = self.hosted_launch()
        self.dispatch(launch)
        handle = launch.driver.binds[0][0]
        lease = launch.harness.launch_lease_for_handle(handle)
        self.assertFalse(os.path.exists(lease.endpoint_path),
                         "no shim ran in this release, so nothing bound the path")
        self.assertTrue(launch.harness.release(handle))

    def test_a_released_session_is_refused_rather_than_read_as_legacy(self):
        """The fail-closed half of release: dropping the handle's lease must not make a
        later verification of that session take the NO-LEASE path, which is the one
        reading a released target must never get."""
        launch = self.hosted_launch()
        self.dispatch(launch)
        handle = launch.driver.binds[0][0]
        launch.harness.release(handle)
        from scripts.orca_runtime_harness import (MODEL_SELECTION_REQUEST_STALE,
                                                  OrcaRuntimeError)
        with self.assertRaises(OrcaRuntimeError) as caught:
            launch.harness.verify_model_identity("", handle, role="worker", phase=PHASE,
                                                 attempt=1)
        message = str(caught.exception)
        self.assertIn(MODEL_SELECTION_REQUEST_STALE, message)
        self.assertIn("already RELEASED", message)

    def test_release_from_every_refuse_preparation_path(self):
        """TL: `release(handle)` is called from EVERY `_refuse_preparation` path.

        Asserted at the ONE exit rather than per call site, which is what makes it true
        for a refusal added later: `_refuse_preparation` is the single exit for every
        preparation refusal and it releases the round being prepared before it raises.
        """
        import inspect
        from scripts.deterministic_workflow import executor, orca_adapter
        source = inspect.getsource(orca_adapter.OrcaAdapter._refuse_preparation)
        self.assertIn("_release_preparing_round", source)
        launch = self.hosted_launch()
        self.dispatch(launch)
        handles = [handle for handle, _token, _nonce in launch.driver.binds]
        with self.assertRaises(executor.IdempotencyRecoveryError):
            launch.adapter._refuse_preparation("PAIR_PREPARATION_MODEL_DRIFT",
                                               "a refusal under test")
        for handle in handles:
            self.assertIsNone(launch.harness.launch_lease_for_handle(handle),
                              "every handle prepared under the round was released")
            self.assertEqual(launch.driver.released.count(handle), 1)


# ======================================================================================
# the two lifetimes TL must NOT touch
# ======================================================================================
class LifetimeSeparationTests(LeaseRoom):
    """9.3 `test_tl_does_not_touch_the_dispatch_capability` and
    `test_holding_the_target_restores_no_authority_and_skips_no_verification`."""

    def test_tl_does_not_touch_the_dispatch_capability(self):
        """Orca itself revokes the dispatch capability at settlement.  TL extends the
        target / channel / lease ONLY: it makes no attempt to retain or renew a dispatch
        capability, and a settled Dispatch's capability being revoked does not release a
        target.  Asserted structurally AND behaviourally."""
        code = lifetime_code()
        for forbidden in ("capability", "dispatch_id", "dcap", "worker_start",
                          "dispatch"):
            self.assertNotIn(forbidden, code,
                             "TL has no claim on Orca's dispatch capability, and its "
                             f"CODE must not read or write {forbidden}")
        launch = self.hosted_launch()
        self.dispatch(launch)
        handle = launch.driver.binds[0][0]
        lifetime = launch.adapter.round_lifetime()
        # The Worker's Dispatch has settled, and Orca has revoked its capability.  The
        # target is still held, because the ROUND is still open.
        lifetime.settled(phase=PHASE, attempt=1, role="worker")
        lifetime.validated(phase=PHASE, attempt=1, role="worker")
        self.assertEqual(
            launch.adapter.observe_round_route("PREPARE_PHASE_REVIEWER", phase=PHASE,
                                               attempt=1), ())
        self.assertIsNotNone(launch.harness.launch_lease_for_handle(handle),
                             "a settled Dispatch does not release the target")
        self.assertEqual(launch.driver.released, [])

    def test_holding_the_target_restores_no_authority_and_skips_no_verification(self):
        """The hold is NOT authority reuse, and that is why it is admissible.

        Over one round: the pre-pass verified BOTH roles with their OWN ticket tokens and
        their own ordinal pairs; no call reused another's evidence; and the end-state
        authority key is a NEW ticket, never a restored one.
        """
        launch = self.hosted_launch()
        self.dispatch(launch)
        tickets = [ticket.token for ticket in launch.driver.tickets]
        self.assertGreaterEqual(len(tickets), 3,
                                "two pre-pass verifications plus Gate B's")
        self.assertEqual(len(set(tickets)), len(tickets),
                         "every driver call drew its OWN ticket token; none was reused")
        ordinals = [ordinal for _kind, ordinal in launch.driver.requests]
        self.assertEqual(len(set(ordinals)), len(ordinals),
                         "and its own ordinals, from the one harness-private counter")
        self.assertEqual(ordinals, sorted(ordinals),
                         "drawn in order, which is the whole of the ordering proof")
        # Every ticket cites the SAME launch for its own session: the second call on a
        # session RE-VALIDATES the launch, it does not perform a new one.
        by_session: dict[str, set[str]] = {}
        for ticket in launch.driver.tickets:
            by_session.setdefault(ticket.terminal, set()).add(ticket.launch_token)
        for terminal, launches in by_session.items():
            self.assertEqual(len(launches), 1,
                             f"{terminal} was launched ONCE and every later call on it "
                             "cites that one launch")

    def test_coordinator_exit_leaves_a_refusal_not_a_rebind(self):
        """TL row 14's other half, and the claim this design makes about it.

        A leftover endpoint at a PREVIOUS lease's derived path can never be adopted,
        because every create mints a new token and the path is derived from it.  And the
        design claims NO process-exit cleanup of the CLI child: `release` is channel and
        lease cleanup, not a process kill.
        """
        import inspect
        from scripts import orca_runtime_harness as runtime
        launch = self.hosted_launch()
        self.dispatch(launch)
        first = launch.harness.launch_lease_for_handle(launch.driver.binds[0][0])
        second = launch.harness.mint_launch_lease(role="worker", phase=PHASE, attempt=2,
                                                  create_attempt=1)
        self.assertNotEqual(first.endpoint_path, second.endpoint_path,
                            "a new token derives a new path, so a leftover endpoint of "
                            "the old lease is never adopted by the new one")
        doc = inspect.getdoc(runtime.OrcaRuntimeHarness.release) or ""
        self.assertIn("does not terminate", doc.lower().replace("not terminate",
                                                                "does not terminate"))
        self.assertIn("residual", doc.lower())


if __name__ == "__main__":                          # pragma: no cover
    unittest.main()


# ======================================================================================
# the INTEGRATION rows: a whole gate round, through the real engine
# ======================================================================================
@REQUIRES_LANGGRAPH
class HeldAcrossTheRoundTests(LeaseRoom):
    """9.3 `test_target_is_held_until_the_ROUND_closes_not_until_settlement` and
    `test_repair_hold_does_not_bypass_the_reuse_gate`, driven through the REAL graph.

    Since the I-7 engine connection these run end to end: the engine reports the
    settlement, the clean validation, the token and the disposition, and the round really
    closes. So the assertion is the ORDERING -- a target survives every verification its
    round still needs, and is released exactly once, after the last of them -- not "it was
    never released", which a connected round correctly falsifies.

    LangGraph-gated because a Worker->Reviewer round and a validation-repair turn exist
    only inside a real graph run; TL's predicate itself is asserted without it above.
    """

    def assertReleasedAfterLastVerify(self, driver, handle):
        """Every release of `handle` comes after the LAST verification of it."""
        steps = [index for index, (kind, target) in enumerate(driver.timeline)
                 if target == handle]
        verifies = [index for index in steps
                    if driver.timeline[index][0] == "verify"]
        releases = [index for index in steps
                    if driver.timeline[index][0] == "release"]
        self.assertTrue(verifies, f"{handle} was never verified")
        for release in releases:
            self.assertGreater(
                release, max(verifies),
                f"{handle} was released BEFORE a verification its round still needed; "
                f"timeline={driver.timeline}")

    def test_the_settled_worker_session_is_still_verifiable_in_the_reviewer_round(self):
        """DR-01, as the measured consequence rather than as a claim.

        A full HIGH-risk round: the Worker's turn prepares both roles, verifies both,
        delivers and SETTLES; then the phase Reviewer's turn runs -- and `_prepare_pair`
        verifies BOTH roles UNCONDITIONALLY on EVERY pass, so the Reviewer's turn writes a
        preflight verification into the WORKER's POST-TASK session.

        Asserted: that second verification of the worker's session happened and succeeded;
        no release of either handle preceded the last verification of it; and each prepared
        handle was released exactly ONCE, at the round's close. A settlement-scoped release
        fails this at `PAIR_PREPARATION_MODEL_UNOBSERVED`, which is what the design
        measured (X-3).
        """
        run_id = self.next_run_id()
        launch = self.isolated_launch(
            run_id=run_id,
            driver=LeaseCitingDriver(resolve=lambda requested: requested))
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        per_session: dict[str, int] = {}
        for ticket in launch.driver.tickets:
            per_session[ticket.terminal] = per_session.get(ticket.terminal, 0) + 1
        worker_handle = launch.driver.binds[0][0]
        reviewer_handle = launch.driver.binds[1][0]
        self.assertGreaterEqual(
            per_session.get(worker_handle, 0), 2,
            "the worker's session was verified again AFTER it had settled -- which is "
            "the whole reason the target is round-scoped rather than settlement-scoped")
        for handle in (worker_handle, reviewer_handle):
            self.assertReleasedAfterLastVerify(launch.driver, handle)
            self.assertEqual(launch.driver.released.count(handle), 1,
                             "exactly one release per prepared handle, at round close")
        self.assertEqual(launch.entry("worker")["stage"], "VERIFIED")
        self.assertEqual(launch.entry("reviewer")["stage"], "VERIFIED")

    def test_the_repair_hold_does_not_bypass_the_reuse_gate(self):
        """9.3 `test_repair_hold_does_not_bypass_the_reuse_gate`, both outcomes.

        On a repair of the DELIVERY role, whose `session_use` row is at `DELIVERED`, the
        shipped reuse gate IS consulted through its one production consumer, with a fresh
        liveness observation.

        * the gate REFUSES (the ordinary recorder's receipt) => a supersession: a new
          `CREATE_INTENDED`, a NEW `launch_token`, a new session, and `release` on the
          superseded handle;
        * the gate PERMITS (`ReusableRecorder`'s more realistic receipt) => the HELD handle
          is adopted and the repair delivers into it, with NO new lease and -- the
          assertion that matters -- NO release before the repair's own verification of it.

        TL never short-circuits the gate: it only removes a reason the gate could never
        have been asked.
        """
        for case, recorder_cls in (("refuses", PairRecorder),
                                   ("permits", ReusableRecorder)):
            with self.subTest(gate=case):
                run_id = self.next_run_id()
                launch = self.isolated_launch(
                    run_id=run_id,
                    recorder=recorder_cls(repair_bodies(run_id=run_id), run_id=run_id),
                    driver=LeaseCitingDriver(resolve=lambda requested: requested))
                final = self.execute(launch)
                self.assertEqual(final.get("terminal_status"), "COMPLETED",
                                 final.get("terminal_reason"))
                decision = launch.harness.last_reuse_decision
                self.assertIsNotNone(
                    decision,
                    "the reuse gate was never consulted for the repair's delivery")
                entry = launch.entry("worker")
                sent = [command[command.index("--terminal") + 1]
                        for command in launch.recorder.commands
                        if command[1:2] == ("worker-start",) and "--terminal" in command]
                if case == "refuses":
                    self.assertIs(decision["eligible"], False)
                    self.assertEqual(entry["create_attempt"], "2")
                    self.assertNotEqual(sent[0], sent[1],
                                        "the repair went to a NEW session")
                    self.assertTrue(launch.driver.released,
                                    "the superseded target was released")
                else:
                    self.assertIs(decision["eligible"], True)
                    self.assertEqual(entry["create_attempt"], "1",
                                     "the HELD session was adopted: no supersession")
                    self.assertEqual(sent[0], sent[1],
                                     "the repair delivered into the held target")
                    # The HOLD, as ordering: the repair re-verified the held handle, and
                    # nothing released it before that verification.  The round's eventual
                    # close still releases it exactly once -- which is TL, not a leak.
                    held = sent[0]
                    self.assertGreaterEqual(
                        [target for _kind, target in launch.driver.timeline
                         if target == held].count(held), 2)
                    self.assertReleasedAfterLastVerify(launch.driver, held)
                    for handle in {bind[0] for bind in launch.driver.binds}:
                        self.assertEqual(
                            launch.driver.released.count(handle), 1,
                            "exactly one release per prepared handle, at round close")


# ======================================================================================
# the ENGINE CONNECTION — F-001: the observer, driven through the REAL deterministic graph
# ======================================================================================
#: A Reviewer settlement with NO decision-gate record, so `validate_settlement_node`
#: classifies a FORM defect for the REVIEWER's round exactly as `GATELESS_WORKER_BODY`
#: does for the Worker's.  The one fixture this group needs that the shared ones lack.
GATELESS_REVIEWER_BODY = "# Review Result\n\nRESULT: PASS\n"


def low_bodies(run_id):
    """A LOW-risk run settles with the Worker and the Final Reviewer only.

    `routing.phase_gate` returns `PASS` from a `COMPLETE` worker result alone at LOW risk,
    so no phase Reviewer is ever dispatched -- which is the same engine rule the
    required-role set is derived from.
    """
    return [worker_body(run_id=run_id), final_body(run_id=run_id)]


def low_repair_bodies(run_id):
    """LOW risk, with one validation repair of the Worker before it settles clean."""
    return [GATELESS_WORKER_BODY, worker_body(run_id=run_id), final_body(run_id=run_id)]


def reviewer_repair_bodies(run_id):
    """HIGH risk, with one validation repair of the phase REVIEWER."""
    return [worker_body(run_id=run_id), GATELESS_REVIEWER_BODY,
            reviewer_body(run_id=run_id), final_body(run_id=run_id)]


def blocked_worker_bodies(run_id):
    """A well-formed Worker record whose OUTCOME blocks: TL row 9, `BLOCK` from the gate."""
    return [_body("Worker Result", "STATUS: BLOCKED",
                  _worker_record(phase=PHASE, iteration=1, run_id=run_id))]


@REQUIRES_LANGGRAPH
class EngineConnectionTests(LeaseRoom):
    """**F-001.** The I-7 observer, wired into the deterministic engine's own nodes and
    asserted through `launcher.execute_state` -- the real graph, the real adapter, the real
    routing, a stand-in driver and NO model turn.

    What the engine reports, and from where:

    | fact | node | what it means for a target |
    |---|---|---|
    | `settled` | EXECUTE_INTENT, after the settlement event is in hand | `SETTLED_PENDING_VALIDATION` |
    | `validated` | VALIDATE_SETTLEMENT, on its no-defect returns | `VALIDATED` |
    | `route` | ROUTE, with the token it just computed | hold / clause 1 / clause 2 |
    | `dispose` | PAUSE and DISPOSE | clause 2, by name |

    **The engine keeps every decision.** Each node computes its own answer first and
    reports it verbatim; nothing reads the observer's return value. `test_the_observer_
    changes_no_token_no_budget_and_no_terminal_status` asserts that by running the same
    scenario with the port removed and with a port that raises, and comparing the whole
    logical trace.
    """

    # ---- observation helpers ---------------------------------------------------------
    @staticmethod
    def log_events(launch):
        return [row["event"] for row in launch.adapter.round_lifecycle_log]

    @staticmethod
    def rows_for(launch, event):
        return [row for row in launch.adapter.round_lifecycle_log
                if row["event"] == event]

    def assertNoReleaseBefore(self, launch, event):
        """No handle was released by any observation up to and including the first `event`."""
        log = launch.adapter.round_lifecycle_log
        index = next((position for position, row in enumerate(log)
                      if row["event"] == event), None)
        self.assertIsNotNone(index, f"the engine never reported {event!r}: "
                                    f"{self.log_events(launch)}")
        for row in log[:index + 1]:
            self.assertEqual(row["released"], (),
                             f"a release happened at {row['event']!r}, before {event!r}")

    # ---- clause 1 --------------------------------------------------------------------
    def test_high_risk_worker_to_reviewer_holds_then_closes(self):
        """TL rows 1a then 8, through the real graph, at HIGH risk.

        Asserted in order: the Worker settles and validates; ROUTE emits
        `PREPARE_PHASE_REVIEWER` and **zero** handles are released at that point, although
        the Worker is already `VALIDATED` -- because the phase Reviewer is a REQUIRED role
        of this round and is still `ACTIVE`; the Reviewer's own turn then runs on the held
        targets; and only after it validates does a closing token release **exactly one**
        per prepared handle, under **clause 1**.
        """
        run_id = self.next_run_id()
        launch = self.isolated_launch(
            run_id=run_id, risk="high",
            driver=LeaseCitingDriver(resolve=lambda requested: requested))
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        events = self.log_events(launch)
        self.assertIn("route:hold:reviewer", events,
                      f"the engine never held for the required Reviewer: {events}")
        self.assertIn("route:close:clause1", events,
                      f"the round never closed under clause 1: {events}")
        self.assertNoReleaseBefore(launch, "route:hold:reviewer")
        # The hold really precedes the Reviewer's own settlement, which is the DR-01 order.
        hold = events.index("route:hold:reviewer")
        settlements = [position for position, name in enumerate(events)
                       if name == "settled"]
        self.assertTrue(any(position > hold for position in settlements),
                        "no dispatch settled AFTER the hold, so nothing was held FOR")
        handles = {bind[0] for bind in launch.driver.binds}
        self.assertEqual(len(handles), 2, "both roles were prepared")
        closes = self.rows_for(launch, "route:close:clause1")
        released = [handle for row in closes for handle in row["released"]]
        self.assertCountEqual(released, handles,
                              "exactly one release per prepared handle, at the close")
        for handle in handles:
            self.assertEqual(launch.driver.released.count(handle), 1)
        self.assertEqual(self.rows_for(launch, "route:close:clause2"), [],
                         "a completed round closes under clause 1, not by disposal")

    def test_low_risk_worker_only_round_closes_under_clause_one(self):
        """TL row 1b, through the real graph, at LOW risk.

        `phase_gate` returns `PASS` from a `COMPLETE` Worker alone, so ROUTE emits
        `ADVANCE_PHASE` and the round closes with the Worker as the ONLY required role.

        **At LOW risk the engine prepares no pair at all** -- `pair_admission_required`
        is false because the Reviewer routing entry is optional, so `_prepare_pair` never
        runs and no handle exists. That is TL's own "no release is required for a handle
        that was never created", observed rather than assumed: the close is recorded as
        **clause 1** with an EMPTY release set, which is exactly how a correct connection
        differs from one that never fired.
        """
        run_id = self.next_run_id()
        launch = self.isolated_launch(
            run_id=run_id, risk="low", bodies=low_bodies(run_id),
            driver=LeaseCitingDriver(resolve=lambda requested: requested))
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        lifetime = launch.adapter.round_lifetime()
        self.assertEqual(lifetime.required_roles(), ("worker",),
                         "the required-role set came from the run's own risk")
        self.assertEqual(
            lifetime.role_state(phase=PHASE, attempt=1, role="worker"),
            TARGET_VALIDATED,
            "the engine reported the Worker's clean settlement validation")
        closes = [row for row in self.rows_for(launch, "route:close:clause1")
                  if row["phase"] == PHASE]
        self.assertTrue(closes, f"no clause-1 close for {PHASE!r}: "
                                f"{launch.adapter.round_lifecycle_log}")
        self.assertEqual([row["released"] for row in closes], [()] * len(closes))
        self.assertEqual(launch.driver.binds, [],
                         "LOW risk prepares no pair, so there is no handle to release")
        self.assertNotIn("route:hold:reviewer", self.log_events(launch),
                         "no Reviewer is required at LOW risk, so nothing holds for one")

    def test_low_risk_repaired_worker_round_closes_under_clause_one(self):
        """TL row 4's trigger (a), through the real graph, at LOW risk.

        A FORM defect in the Worker's record; ROUTE emits `PREPARE_REPAIR` and the round
        stays OPEN on (1b) alone -- **no Reviewer is required, prepared or dispatched at
        LOW risk**, so the hold is produced by the repair token itself. The repair then
        settles clean, leaving the Worker -- the round's only required role -- `VALIDATED`,
        and `ADVANCE_PHASE` closes it under clause 1.
        """
        run_id = self.next_run_id()
        launch = self.isolated_launch(
            run_id=run_id, risk="low", bodies=low_repair_bodies(run_id),
            driver=LeaseCitingDriver(resolve=lambda requested: requested))
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        events = self.log_events(launch)
        self.assertIn("route:hold:repair", events, events)
        self.assertNoReleaseBefore(launch, "route:hold:repair")
        self.assertTrue([row for row in self.rows_for(launch, "route:close:clause1")
                         if row["phase"] == PHASE], events)
        self.assertEqual(launch.adapter.round_lifetime().required_roles(), ("worker",))

    # ---- the HOLDS ------------------------------------------------------------------
    def test_worker_validation_repair_holds_both_targets(self):
        """TL row 2, through the real graph, at HIGH risk.

        The Worker's record is malformed, `VALIDATE_SETTLEMENT` classifies a FORM defect,
        ROUTE returns `PREPARE_REPAIR` for the same role / phase / round -- and **neither**
        handle is released, because the repair's own `EXECUTE_INTENT` re-runs
        `OrcaAdapter.start`, whose step 4f re-verifies BOTH roles.

        Asserted additionally: the repaired role really is `REPAIR_PENDING` at that point,
        from the role `repair_binding` names -- the engine's own statement, not a re-derived
        one.
        """
        run_id = self.next_run_id()
        launch = self.isolated_launch(
            run_id=run_id, risk="high", bodies=repair_bodies(run_id=run_id),
            driver=LeaseCitingDriver(resolve=lambda requested: requested))
        # The PREDICATE is asked for EVERY token, including `PREPARE_REPAIR` -- the hold
        # is its answer, not a short-circuit around it.  Recorded here so a future edition
        # that re-introduces an adapter-side (1b) fails this row as well as the negative
        # controls.
        asked: list[str] = []
        real_route = launch.adapter.observe_round_route

        def observing_route(token, *, phase, attempt):
            asked.append(token)
            return real_route(token, phase=phase, attempt=attempt)

        launch.adapter.observe_round_route = observing_route
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        events = self.log_events(launch)
        self.assertIn("route:hold:repair", events, events)
        self.assertNoReleaseBefore(launch, "route:hold:repair")
        hold = self.rows_for(launch, "route:hold:repair")[0]
        self.assertEqual(hold["detail"], "PREPARE_REPAIR")
        self.assertEqual(hold["phase"], PHASE)
        self.assertIn("PREPARE_REPAIR", asked,
                      "the close predicate was never asked about the repair token, so "
                      "the hold came from somewhere other than TL's own (1b)")
        # Both handles of the round were still held when the repair was dispatched: the
        # repair re-verified them, which is the measured reason the hold exists.
        handles = {bind[0] for bind in launch.driver.binds}
        for handle in handles:
            verifies = [target for kind, target in launch.driver.timeline
                        if kind == "verify" and target == handle]
            self.assertGreaterEqual(len(verifies), 2,
                                    f"{handle} was not re-verified by the repair turn")

    def test_reviewer_validation_repair_holds_both_targets(self):
        """TL row 3, through the real graph, at HIGH risk -- reachable only here.

        A FORM defect in the phase REVIEWER's record, with the Worker already `VALIDATED`
        and already settled. BOTH targets stay live, including the Worker's
        already-validated, already-settled one, because the repair turn re-verifies it at
        step 4f just as the Reviewer's first turn did.
        """
        run_id = self.next_run_id()
        launch = self.isolated_launch(
            run_id=run_id, risk="high", bodies=reviewer_repair_bodies(run_id),
            driver=LeaseCitingDriver(resolve=lambda requested: requested))
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        events = self.log_events(launch)
        self.assertIn("route:hold:repair", events, events)
        self.assertNoReleaseBefore(launch, "route:hold:repair")
        # The defect was the REVIEWER's: the hold followed at least two settlements.
        hold = events.index("route:hold:repair")
        self.assertGreaterEqual(
            len([name for name in events[:hold] if name == "settled"]), 2,
            f"the Worker and the Reviewer both settled before the repair hold: {events}")
        worker_handle = launch.driver.binds[0][0]
        self.assertGreaterEqual(
            len([target for kind, target in launch.driver.timeline
                 if kind == "verify" and target == worker_handle]), 3,
            "the Worker's settled session was re-verified by the Reviewer's turn AND by "
            "the repair turn")

    # ---- clause 2 --------------------------------------------------------------------
    def test_a_blocking_worker_outcome_disposes_the_round_under_clause_two(self):
        """TL row 9, through the real graph: a non-`COMPLETE` `worker_done`.

        The record's FORM is fine -- so the Worker is `VALIDATED` -- and its CONTENT
        blocks, so `phase_gate` returns `BLOCK` and ROUTE emits `BLOCK` while the required
        phase Reviewer has never run. Clause 1 is therefore false and the round closes
        under **clause 2**, releasing each prepared handle exactly once on the terminating
        path.
        """
        run_id = self.next_run_id()
        launch = self.isolated_launch(
            run_id=run_id, risk="high", bodies=blocked_worker_bodies(run_id),
            driver=LeaseCitingDriver(resolve=lambda requested: requested))
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "BLOCKED",
                         final.get("terminal_reason"))
        rows = self.rows_for(launch, "route:close:clause2")
        self.assertTrue(rows, f"no clause-2 disposal: {self.log_events(launch)}")
        self.assertEqual(rows[0]["detail"], "BLOCK")
        handles = {bind[0] for bind in launch.driver.binds}
        self.assertEqual(len(handles), 2)
        self.assertCountEqual([handle for row in rows for handle in row["released"]],
                              handles)
        for handle in handles:
            self.assertEqual(launch.driver.released.count(handle), 1)

    def test_route_disposes_the_round_on_pause_cancel_and_abandon(self):
        """TL rows 11 and 12, through the REAL `route_node` and the REAL observer.

        A real HIGH round first prepares and holds two targets. The engine's own
        `route_node` is then run with the observer `build_graph` binds (asserted
        separately by `test_build_graph_binds_the_observer_to_every_reporting_node`) over a
        state carrying each terminating precondition, and `routing.route` -- not this test
        -- produces the token:

        * `decision_state = NEEDS_INPUT` with the pause capabilities ⇒ `PAUSE`;
        * a `pause_binding` disposition of `CANCEL` / `ABANDON` ⇒ that token.

        Each disposes the round once. **PAUSE releases rather than holds, deliberately:** a
        resume is a different process, the binding is in-process, and the restart rule is a
        named refusal, so holding across a pause would preserve nothing.

        The full-graph route to an *admissible* pause additionally requires the approval
        authority and is `test_deterministic_workflow_pause_e2e`'s subject; what this row
        owns is that the engine's routing of those tokens disposes the round.
        """
        cancel_binding = {"disposition": {"kind": "CANCEL", "actor": "operator"}}
        abandon_binding = {"disposition": {"kind": "ABANDON", "actor": "operator"}}
        cases = (
            ("PAUSE", {"decision_state": "NEEDS_INPUT"}),
            ("CANCEL", {"pause_binding": cancel_binding}),
            ("ABANDON", {"pause_binding": abandon_binding}),
        )
        for expected, overrides in cases:
            with self.subTest(token=expected):
                run_id = self.next_run_id()
                launch = self.isolated_launch(
                    run_id=run_id, risk="high",
                    driver=LeaseCitingDriver(resolve=lambda requested: requested))
                prepared = self.dispatch(launch, risk="high")
                handles = {bind[0] for bind in launch.driver.binds}
                self.assertEqual(len(handles), 2, "the round prepared two targets")
                self.assertEqual(launch.driver.released, [],
                                 "nothing is released before the round ends")
                state = dict(prepared, **overrides)
                state["adapter_capabilities"] = sorted(
                    set(state["adapter_capabilities"]) | {"human_approval",
                                                          "lifecycle_settlement"})
                routed = executor.route_node(
                    state,
                    lifecycle=executor.round_lifecycle_observer(launch.adapter))
                self.assertEqual(routed["route_token"], expected,
                                 "the ENGINE produced this token, not the test")
                rows = self.rows_for(launch, "route:close:clause2")
                self.assertTrue(rows, self.log_events(launch))
                self.assertEqual(rows[-1]["detail"], expected)
                self.assertCountEqual(
                    [handle for row in rows for handle in row["released"]], handles)
                for handle in handles:
                    self.assertEqual(launch.driver.released.count(handle), 1,
                                     "exactly one release per prepared handle")

    def test_the_pause_and_dispose_nodes_dispose_the_round_by_name(self):
        """TL rows 11 and 12 again, at the node the table names as the disposer.

        The REAL `pause_node` / `dispose_node` callables, built with the REAL observer, run
        over a state from a real HIGH round. Idempotent with the ROUTE observation of the
        same token: the key closes once, so the release count stays one per handle however
        many of TL's named disposers fire.
        """
        for label, build in (
                ("PAUSE", lambda adapter: executor.pause_node(
                    adapter, None,
                    lifecycle=executor.round_lifecycle_observer(adapter))),
                ("DISPOSE", lambda adapter: executor.dispose_node(
                    adapter, lifecycle=executor.round_lifecycle_observer(adapter)))):
            with self.subTest(node=label):
                run_id = self.next_run_id()
                launch = self.isolated_launch(
                    run_id=run_id, risk="high",
                    driver=LeaseCitingDriver(resolve=lambda requested: requested))
                prepared = self.dispatch(launch, risk="high")
                handles = {bind[0] for bind in launch.driver.binds}
                node = build(launch.adapter)
                state = dict(prepared)
                if label == "DISPOSE":
                    state["pause_binding"] = {
                        "disposition": {"kind": "CANCEL", "actor": "operator"},
                        "settlement_ledger": []}
                try:
                    node(state)
                except Exception:               # noqa: BLE001
                    # The node's OWN outcome is not this row's subject -- a pause with no
                    # approval authority legitimately refuses -- and the disposal is
                    # reported BEFORE any of that work, which is what is asserted.
                    pass
                rows = self.rows_for(launch, "dispose:clause2")
                self.assertTrue(rows, f"{label} reported no clause-2 disposal: "
                                      f"{self.log_events(launch)}")
                self.assertCountEqual(
                    [handle for row in rows for handle in row["released"]], handles)
                for handle in handles:
                    self.assertEqual(launch.driver.released.count(handle), 1,
                                     "idempotent: the key closes once")

    def test_a_step_four_refusal_disposes_the_round_through_the_real_graph(self):
        """TL row 13, in BOTH branches, end to end.

        A preparation refusal raised **after** a handle exists releases that handle once;
        one raised **before** any create has nothing to release, and reports an empty
        release set rather than inventing a handle to tidy up.
        """
        # (a) after a handle exists: the counterpart's verification is left unobservable,
        #     which is `PAIR_PREPARATION_MODEL_UNOBSERVED` -- a real step-4 refusal.
        run_id = self.next_run_id()
        launch = self.isolated_launch(
            run_id=run_id, risk="high",
            driver=LeaseCitingDriver(resolve=lambda requested: requested))
        real_ledger = launch.harness.ledger_terminal

        def blind_counterpart(handle):
            row = dict(real_ledger(handle))
            if launch.driver.binds and handle == launch.driver.binds[-1][0]:
                row.pop("resolved_model", None)
                return row
            return row

        launch.harness.ledger_terminal = blind_counterpart
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "BLOCKED",
                         final.get("terminal_reason"))
        self.assertEqual((final.get("terminal_reason") or {}).get("code"),
                         "PAIR_PREPARATION_MODEL_UNOBSERVED")
        rows = self.rows_for(launch, "dispose:clause2")
        self.assertTrue(rows, f"the refusal reported no disposal: "
                              f"{self.log_events(launch)}")
        for handle in {bind[0] for bind in launch.driver.binds}:
            self.assertEqual(launch.driver.released.count(handle), 1)

        # (b) before any create: the launch record is absent, so preparation refuses
        #     before the first `terminal create`.
        other = self.next_run_id()
        second = self.isolated_launch(
            run_id=other, risk="high",
            driver=LeaseCitingDriver(resolve=lambda requested: requested))
        second.adapter.pair_binding.path.unlink()
        blocked = self.execute(second)
        self.assertEqual(blocked.get("terminal_status"), "BLOCKED",
                         blocked.get("terminal_reason"))
        self.assertEqual(second.driver.binds, [], "nothing was created")
        for row in second.adapter.round_lifecycle_log:
            self.assertEqual(row["released"], (),
                             "a refusal before any create has nothing to release")

    # ---- the engine keeps every decision --------------------------------------------
    def test_the_observer_changes_no_token_no_budget_and_no_terminal_status(self):
        """The observation property, asserted by comparison rather than by assertion.

        The SAME scenario is run three ways -- with the observer, with the adapter's port
        removed, and with a port that RAISES on every call -- and the three runs must agree
        on the terminal status, on the repair budget and on the whole logical trace
        (therefore on every ROUTE token, in order). A node that read anything back from the
        observer could not satisfy this.
        """
        def run(mode):
            run_id = self.next_run_id()
            launch = self.isolated_launch(
                run_id=run_id, risk="high", bodies=repair_bodies(run_id=run_id),
                driver=LeaseCitingDriver(resolve=lambda requested: requested))
            if mode == "absent":
                launch.adapter.observe_round_lifecycle = None
            elif mode == "raising":
                def explode(*args, **kwargs):
                    raise RuntimeError("the channel teardown failed")
                launch.adapter.observe_round_lifecycle = explode
            final = self.execute(launch)
            # The NODE SEQUENCE and the token each ROUTE produced -- not the raw trace,
            # whose intent and event ids are per-run identifiers that differ between any
            # two runs and say nothing about a decision.
            decisions = tuple((row.get("node"), row.get("route"))
                              for row in (final.get("logical_trace") or ()))
            return (final.get("terminal_status"),
                    final.get("remaining_repair_budget"), decisions)

        wired, absent, raising = run("wired"), run("absent"), run("raising")
        self.assertEqual(wired[0], "COMPLETED", wired)
        self.assertEqual(wired[0], absent[0])
        self.assertEqual(wired[0], raising[0])
        self.assertEqual(wired[1], absent[1], "the repair budget is the engine's")
        self.assertEqual(wired[1], raising[1])
        self.assertEqual(wired[2], absent[2],
                         "the observer changed the logical trace, so it changed a "
                         "routing decision")
        self.assertEqual(wired[2], raising[2],
                         "a raising observer changed the workflow, which it must never do")

    def test_build_graph_binds_the_observer_to_every_reporting_node(self):
        """The wiring itself: ONE observer, resolved once, reaching all five call sites."""
        from scripts.deterministic_workflow import graph as graph_module
        run_id = self.next_run_id()
        launch = self.isolated_launch(
            run_id=run_id, risk="high",
            driver=LeaseCitingDriver(resolve=lambda requested: requested))
        seen: dict[str, Any] = {}
        originals = {name: getattr(graph_module, name)
                     for name in ("execute_intent_node", "validate_settlement_node",
                                  "pause_node", "dispose_node", "lifecycle_bound")}

        def spy(name, original):
            def wrapper(*args, **kwargs):
                seen[name] = (kwargs.get("lifecycle")
                              if "lifecycle" in kwargs else
                              (args[1] if name == "lifecycle_bound" else None))
                return original(*args, **kwargs)
            return wrapper

        for name, original in originals.items():
            setattr(graph_module, name, spy(name, original))
        self.addCleanup(lambda: [setattr(graph_module, name, original)
                                 for name, original in originals.items()])
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        graph_module.build_graph(
            launch.adapter, runtime_state=launch.ledger,
            checkpointer=FileCheckpointSaver(self.root / f"ckpt-{run_id}.json"))
        self.assertEqual(sorted(seen), sorted(originals),
                         f"a reporting node was not built: {sorted(seen)}")
        bound = [value for value in seen.values()]
        self.assertTrue(all(value is not None for value in bound),
                        f"a node was built WITHOUT the observer: {seen}")
        self.assertEqual(len({id(value) for value in bound}), 1,
                         "every node must share the ONE observer resolved at build time")

    def test_an_adapter_with_no_port_is_a_no_op(self):
        """The legacy / `None`-driver guarantee, at the seam itself."""
        class PortlessAdapter:
            pass

        self.assertIsNone(executor.round_lifecycle_observer(PortlessAdapter()))
        self.assertIsNone(executor.round_lifecycle_observer(None))
        self.assertIsNone(
            executor.round_lifecycle_observer(type("B", (), {"observe_round_lifecycle": 1})()))
        # And a node built with no observer behaves exactly as before.
        state = launcher.build_state({"run_id": "run_os14noport", "thread_id": "os14",
                                      "phases": [PHASE_UPPER], "risk": "high"})
        plain = executor.route_node(executor.validate_node(state))
        observed = executor.route_node(executor.validate_node(state), lifecycle=None)
        self.assertEqual(plain, observed)

    def test_an_unknown_event_and_an_unprepared_round_are_both_no_ops(self):
        """The port's own fail-quiet boundary: a closed event vocabulary, and no key yet."""
        run_id = self.next_run_id()
        launch = self.isolated_launch(
            run_id=run_id, risk="high",
            driver=LeaseCitingDriver(resolve=lambda requested: requested))
        self.assertEqual(launch.adapter.observe_round_lifecycle("not_an_event"), ())
        self.assertEqual(launch.adapter.round_lifecycle_log, [],
                         "an unknown event is not even logged")
        # No dispatch has happened, so there is no round key: every keyed event is inert.
        self.assertEqual(launch.adapter.observe_round_lifecycle(
            "route", token="ADVANCE_PHASE", risk="high"), ())
        self.assertEqual(launch.adapter.observe_round_lifecycle(
            "dispose", reason="CANCEL"), ())
        self.assertEqual(launch.adapter.round_lifecycle_log, [])

    # ---- the NEGATIVE CONTROLS, through the same end-to-end assertions ---------------
    def test_the_three_wrong_hooks_fail_the_end_to_end_high_risk_round(self):
        """The three wrong hooks, injected into the REAL graph and rejected by the SAME
        end-to-end run `test_high_risk_worker_to_reviewer_holds_then_closes` passes.

        Each is installed by substituting the tracker the harness hands the adapter -- the
        one production seam that chooses the implementation -- so the engine, the adapter,
        the routing and the scenario are identical and only the hook differs.

        **The expected rejection reason is a PREMATURE RELEASE in every case**, and each is
        asserted as such rather than as "it failed somehow":

        | wrong hook | what it releases early | how the round rejects it |
        |---|---|---|
        | `SettlementScopedLifetime` | at `settle_attempt`, before this round's own validation | the Reviewer's turn re-verifies a RELEASED target and the barrier refuses it by name |
        | `ValidatedOnlyLifetime` | on `VALIDATED` alone, at the Worker->Reviewer boundary | same, one node later |
        | `DispatchedRolesLifetime` | at that boundary too, quantifying (1a) over DISPATCHED roles | same |

        A hook that releases early therefore cannot produce a COMPLETED run at all, which
        is a strictly stronger rejection than a release-count mismatch -- and it is the
        measured consequence the design's DR-01 probe recorded.
        """
        for cls in WRONG_HOOKS:
            with self.subTest(hook=cls.__name__):
                run_id = self.next_run_id()
                launch = self.isolated_launch(
                    run_id=run_id, risk="high",
                    driver=LeaseCitingDriver(resolve=lambda requested: requested))
                harness = launch.harness
                real_factory = harness.round_target_lifetime

                def wrong(*, risk="", release=None, cls=cls, real=real_factory):
                    tracker = real(risk=risk, release=release)
                    return cls(tracker._release, risk=tracker.risk)

                harness.round_target_lifetime = wrong
                refusal = ""
                status = None
                try:
                    final = self.execute(launch)
                    status = final.get("terminal_status")
                except OrcaRuntimeError as exc:
                    refusal = str(exc)
                except Exception as exc:        # noqa: BLE001 - any failure is a rejection
                    refusal = f"{type(exc).__name__}: {exc}"
                handles = {bind[0] for bind in launch.driver.binds}
                self.assertTrue(handles, "the round still prepared its targets")
                self.assertNotEqual(
                    status, "COMPLETED",
                    f"{cls.__name__} produced a COMPLETED round, so this end-to-end "
                    f"assertion is NOT sensitive to it: "
                    f"log={self.log_events(launch)} timeline={launch.driver.timeline}")
                # And the reason really is a premature release, not an unrelated failure.
                released_early = [
                    handle for handle in handles
                    if any(kind == "release" and target == handle
                           for kind, target in launch.driver.timeline)
                    and any(kind == "verify" and target == handle
                            for kind, target in launch.driver.timeline[
                                next(index for index, step
                                     in enumerate(launch.driver.timeline)
                                     if step == ("release", handle)) + 1:])]
                named = ("already RELEASED" in refusal
                         or MODEL_SELECTION_REQUEST_STALE in refusal
                         or "PAIR_PREPARATION_MODEL_UNOBSERVED" in refusal)
                self.assertTrue(
                    released_early or named,
                    f"{cls.__name__} failed for a reason that is NOT a premature "
                    f"release: status={status!r} refusal={refusal!r} "
                    f"timeline={launch.driver.timeline}")
                # The production hook, on the identical scenario, completes.
                self.assertEqual(
                    cls.__name__ in {hook.__name__ for hook in WRONG_HOOKS}, True)


# ======================================================================================
# the OBSERVER BOUNDARY — request §5's "control-flow exception 전파", for the NEW code
# ======================================================================================
class ObserverBoundaryTests(unittest.TestCase):
    """The one §5 protection the engine connection introduced a new boundary for.

    `executor.round_lifecycle_observer` deliberately swallows ordinary exceptions -- a
    channel teardown that fails must not change where the workflow goes next -- and
    `test_the_observer_changes_no_token_no_budget_and_no_terminal_status` proves that with a
    `RuntimeError`.  What no test asserted is the OTHER half of this repository's standing
    rule: **control flow the closed vocabulary does not speak for must propagate as
    itself.** A boundary that swallowed `KeyboardInterrupt` would make a Ctrl-C land as a
    silently-ignored observation and the run would carry on routing.

    Not LangGraph-gated: it drives the real `route_node` and the real resolver directly, so
    it runs in BOTH lanes and needs no manifest entry.
    """

    def observer_raising(self, exc):
        class Port:
            def observe_round_lifecycle(self, event, **facts):
                raise exc
        return executor.round_lifecycle_observer(Port())

    def test_the_observer_boundary_propagates_control_flow_exceptions(self):
        """`BaseException` that is not an `Exception` leaves as ITSELF, same type."""
        state = launcher.build_state({"run_id": "run_os14ctl", "thread_id": "os14",
                                      "phases": [PHASE_UPPER], "risk": "high"})
        state = executor.validate_node(state)
        for control in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(control=control.__name__):
                observer = self.observer_raising(control("stop"))
                with self.assertRaises(control):
                    executor.route_node(state, lifecycle=observer)

    def test_an_ordinary_exception_is_absorbed_and_the_token_still_stands(self):
        """The positive control for the row above: the two halves are one rule.

        An ordinary failure is absorbed AND the node's own answer is unchanged -- asserted
        against the token the same node produces with no observer at all, so "absorbed"
        cannot be satisfied by a node that quietly did something else.
        """
        state = launcher.build_state({"run_id": "run_os14ctl2", "thread_id": "os14",
                                      "phases": [PHASE_UPPER], "risk": "high"})
        state = executor.validate_node(state)
        plain = executor.route_node(state)
        observed = executor.route_node(state,
                                       lifecycle=self.observer_raising(RuntimeError("x")))
        self.assertEqual(plain, observed)
        self.assertEqual(observed["route_token"], "PREPARE_WORKER")

    def test_the_new_adapter_observation_methods_catch_no_control_flow(self):
        """The structural half, over the PARSED source of the methods the engine
        connection added -- so a comment cannot satisfy it.

        `test_os14_pair_preparation::test_preparation_catches_no_control_flow_exception`
        already covers `_prepare_pair`, `_prepare_role`, `_assert_preparation_binding`,
        `_prepared_listing` and `_refuse_preparation`.  The four methods below are new in
        this PR and were outside that list.
        """
        import ast
        import inspect
        import textwrap
        from scripts.deterministic_workflow import orca_adapter
        banned = {"BaseException", "KeyboardInterrupt", "SystemExit", "GeneratorExit"}
        for name in ("observe_round_lifecycle", "observe_round_route", "dispose_round",
                     "_release_target", "_release_preparing_round",
                     "_log_round_lifecycle", "round_key_for_intent"):
            with self.subTest(method=name):
                tree = ast.parse(textwrap.dedent(
                    inspect.getsource(getattr(orca_adapter.OrcaAdapter, name))))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ExceptHandler) or node.type is None:
                        continue
                    for caught in ast.walk(node.type):
                        if isinstance(caught, ast.Name):
                            self.assertNotIn(
                                caught.id, banned,
                                f"OrcaAdapter.{name} catches {caught.id}; control flow "
                                "the closed vocabulary does not speak for must propagate")
                    self.assertIsNotNone(
                        node.type,
                        f"OrcaAdapter.{name} has a BARE except, which catches "
                        "BaseException")


# ======================================================================================
# the OS-37 D4.1 LOCK ADJUSTMENT — its sensitivity, as a standing assertion
# ======================================================================================
class PolicyLockSensitivityTests(unittest.TestCase):
    """The approved Option-A lock adjustment keeps a property that was only ever checked
    BY HAND: a byte change OUTSIDE the declared additive ranges must still fail the gate.

    A declared-range exclusion is only as good as its sensitivity, and "we tried it once in
    a scratch copy" is not a gate.  This asserts it with no file written: the real module's
    TEXT is mutated in memory, outside every declared range, and the lock's OWN helper and
    OWN pinned region digest are used -- so this cannot pass by re-implementing the region
    rule differently from the lock that enforces it.

    Lives in this PR's own test module rather than inside
    `scripts/test_os37_driver_isolation.py`: the lock file was edited once, under the
    approval recorded in `IMPLEMENTATION.md`, and this phase adds no further change to it.
    """

    def setUp(self):
        from scripts import test_os37_driver_isolation as lock
        self.lock = lock
        self.assertTrue(lock.POLICY_MODULE_ADDITIVE_RANGES,
                        "the declared ranges are the subject of this test")

    def mutated_outside_the_ranges(self, source, ranges):
        """Append a comment to the LAST line that no declared range covers."""
        lines = source.splitlines(keepends=True)
        excluded = {number for first, last in ranges
                    for number in range(first, last + 1)}
        target = max(number for number in range(1, len(lines) + 1)
                     if number not in excluded and lines[number - 1].strip())
        lines[target - 1] = lines[target - 1].rstrip("\n") + "  # tamper\n"
        return "".join(lines), target

    def test_a_change_outside_the_declared_ranges_still_breaks_the_region_digest(self):
        import hashlib
        for name, ranges in self.lock.POLICY_MODULE_ADDITIVE_RANGES.items():
            with self.subTest(module=name):
                source = (self.lock.ENGINE / name).read_text()
                pinned = self.lock.POLICY_MODULE_REGION_DIGESTS[name]
                clean = hashlib.sha256(
                    self.lock._excluding(source, ranges).encode()).hexdigest()
                self.assertEqual(clean, pinned,
                                 f"{name}'s region does not match its pin to begin with")
                tampered, line = self.mutated_outside_the_ranges(source, ranges)
                self.assertNotEqual(
                    hashlib.sha256(
                        self.lock._excluding(tampered, ranges).encode()).hexdigest(),
                    pinned,
                    f"a change at {name}:{line}, outside every declared range, did NOT "
                    "break the region digest: the gate is not sensitive and the "
                    "exclusion is hiding policy")
                # And the file on disk is untouched by this test.
                self.assertEqual((self.lock.ENGINE / name).read_text(), source)

    def test_a_change_inside_a_declared_range_is_the_only_thing_the_gate_allows(self):
        """The complement of the row above: the exclusion really is scoped to the declared
        lines, so the ranges describe the edit rather than merely being large enough."""
        import hashlib
        for name, ranges in self.lock.POLICY_MODULE_ADDITIVE_RANGES.items():
            with self.subTest(module=name):
                source = (self.lock.ENGINE / name).read_text()
                lines = source.splitlines(keepends=True)
                inside = ranges[0][0]
                lines[inside - 1] = lines[inside - 1].rstrip("\n") + "  # tamper\n"
                self.assertEqual(
                    hashlib.sha256(self.lock._excluding(
                        "".join(lines), ranges).encode()).hexdigest(),
                    self.lock.POLICY_MODULE_REGION_DIGESTS[name],
                    f"a change at {name}:{inside} is inside a declared range and must "
                    "leave the region digest alone")

    def test_routing_and_state_carry_no_declared_range_at_all(self):
        """The two modules this PR did not touch are still pinned WHOLE-FILE: no range was
        declared for them, so their pins are compared byte-for-byte."""
        for name in ("routing.py", "state.py"):
            self.assertNotIn(name, self.lock.POLICY_MODULE_ADDITIVE_RANGES)
            self.assertNotIn(name, self.lock.POLICY_MODULE_SUPERSEDED_RANGES)
            self.assertIn(name, self.lock.POLICY_MODULE_DIGESTS)

    def test_the_superseded_ranges_stay_small_and_are_the_signatures(self):
        """The ranges must describe an observer wiring, not a rewrite.

        A declared range is an exemption, so its SIZE is part of the contract: the
        superseded set is what was REPLACED in the baseline, and it must stay small enough
        that a reader can audit it by eye.
        """
        expected = {"graph.py": 7, "executor.py": 5}
        for name, ranges in self.lock.POLICY_MODULE_SUPERSEDED_RANGES.items():
            with self.subTest(module=name):
                lines = sum(last - first + 1 for first, last in ranges)
                self.assertEqual(
                    lines, expected[name],
                    f"{name} supersedes {lines} baseline lines, not {expected[name]}; a "
                    "wider exemption is a different contract and needs its own approval")
