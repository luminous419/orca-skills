"""OS-43 U-6: the ordered classifier, its import guards, and the UR-3 mutation tests.

**The enforced anti-requirement.**  A suite whose only positive assertions are over healthy
runs -- O-1's input, where F5, F6 and F9 all hold -- passes unchanged under M1, M2 and M3
and does NOT satisfy UR-3.  W1, W2, W3 and W4 therefore appear here as NAMED fixtures, each
mutation is CONSTRUCTED and EXECUTED, and each mutant is asserted to be KILLED by yielding a
different state on the same witness.  A test that passes under the mutation is worthless,
and this file says so in the assertion messages.
"""
from __future__ import annotations

import unittest

from scripts.deterministic_workflow import pause_policy, watchdog_classifier
from scripts.deterministic_workflow.watchdog_classifier import (RULES, Rule,
                                                                ClassifierTableInvalid,
                                                                classify, mutate,
                                                                validate_rule_table)
from scripts.deterministic_workflow.watchdog_observation import (SAFETY_RELEVANT_FACTS,
                                                                 SUPPORT_ABSENT_DECLARED,
                                                                 SUPPORT_UNSUPPORTED)
from scripts.test_os43_fixture import (make_snapshot, witness_w1, witness_w2, witness_w3,
                                       witness_w4)


class TranscribedConstantTests(unittest.TestCase):
    """The literals the core carries instead of importing, pinned against their source."""

    def test_the_pause_continuation_verdict_matches_pause_policy(self):
        self.assertEqual(watchdog_classifier.PAUSE_VERDICT_CONTINUATION_RECOVERABLE,
                         pause_policy.PAUSE_CONTINUATION_RECOVERABLE)

    def test_the_status_authority_literals_match_turn_boundary(self):
        from scripts.deterministic_workflow import turn_boundary, watchdog_observation
        self.assertEqual(watchdog_observation.STATUS_AUTHORITY_CHECKPOINT,
                         turn_boundary.STATUS_AUTHORITY_CHECKPOINT)
        self.assertEqual(watchdog_observation.STATUS_AUTHORITY_PAUSE_RECORD,
                         turn_boundary.STATUS_AUTHORITY_PAUSE_RECORD)
        self.assertEqual(watchdog_observation.STATUS_AUTHORITY_DECLARED,
                         turn_boundary.STATUS_AUTHORITY_DECLARED)

    def test_the_outcome_literals_the_gate_carries_match_the_engine(self):
        from scripts.deterministic_workflow import recovery_runtime, watchdog_state
        self.assertEqual(watchdog_state.RECOVERY_OUTCOMES,
                         recovery_runtime.RECOVERY_OUTCOMES)
        from scripts.deterministic_workflow import coordinator_liveness, watchdog_supervisor
        for name in ("LIVE", "EXPIRED", "ABSENT", "UNREADABLE"):
            self.assertEqual(getattr(watchdog_state, f"LIVENESS_{name}"),
                             getattr(coordinator_liveness, f"LIVENESS_{name}"))
        self.assertEqual(watchdog_supervisor.RECOVERY_KIND_STALLED_ACTIVE,
                         recovery_runtime.RECOVERY_KIND_STALLED_ACTIVE)
        self.assertEqual(watchdog_supervisor.RECOVERY_KIND_PAUSE_CONTINUATION,
                         recovery_runtime.RECOVERY_KIND_PAUSE_CONTINUATION)


class TableShapeTests(unittest.TestCase):
    """UR-1: an ORDERED classifier, and nothing here claims it is a partition."""

    def test_the_table_is_twelve_rules_in_position_order(self):
        self.assertEqual(len(RULES), 12)
        self.assertEqual([rule.index for rule in RULES], list(range(1, 13)))

    def test_only_R6_and_R11_are_actionable(self):
        actionable = {rule.index: rule.state for rule in RULES if rule.actionable}
        self.assertEqual(actionable, {6: "PAUSE_CONTINUATION_RECOVERABLE",
                                      11: "STALLED_RECOVERABLE"})

    def test_runnable_is_borne_by_exactly_one_rule_which_is_R11(self):
        bearers = [rule.index for rule in RULES if "F5" in rule.reads]
        self.assertEqual(bearers, [11],
                         "the F-006 shape is UNWRITABLE, not merely fixed: there is no "
                         "disjunction for F5 to be scoped incorrectly across")

    def test_the_liveness_disjunction_is_split_into_two_states(self):
        self.assertEqual(watchdog_classifier.rule_for("ACTIVE_DISPATCH_WAIT").reads,
                         frozenset({"F6"}))
        self.assertEqual(watchdog_classifier.rule_for("OWNED_ELSEWHERE_OBSERVE").reads,
                         frozenset({"F9"}))

    def test_every_rule_carries_its_stated_reason_into_the_source(self):
        for rule in RULES:
            self.assertTrue(rule.rationale.strip(),
                            f"R{rule.index} moved without its reason")

    def test_the_classifier_is_TOTAL_over_every_fact_vector(self):
        """TOTALITY: R12's predicate is the constant True, so evaluation terminates."""
        import itertools
        names = ("F1", "F2", "F3", "F5", "F6", "F7", "F9", "F10", "F11")
        for combination in itertools.product((False, True), repeat=len(names)):
            true_facts = tuple(name for name, value in zip(names, combination) if value)
            result = classify(make_snapshot(true_facts=true_facts))
            self.assertIn(result.state, {rule.state for rule in RULES})
            self.assertTrue(1 <= result.rule_index <= 12)

    def test_the_rules_are_NOT_asserted_to_be_disjoint(self):
        """UR-1: facts co-occur, and the type says so -- a mapping, not a tagged union."""
        overlapping = make_snapshot(true_facts=("F5", "F6", "F9"))
        matching = [rule.index for rule in RULES if rule.predicate(overlapping)]
        self.assertEqual(matching, [8, 10, 11, 12],
                         "three rules match one input; the ORDER is what decides")
        self.assertEqual(classify(overlapping).rule_index, 8)


class ImportGuardTests(unittest.TestCase):
    """DR-4: guards 1-7 run at IMPORT, so a mutant shape is unwritable in production."""

    def test_the_production_table_passes_every_guard(self):
        validate_rule_table(RULES)

    def test_guard_1_rejects_a_table_whose_indices_are_not_its_positions(self):
        broken = (Rule(2, "A", False, frozenset(), lambda s: True, "x"),)
        with self.assertRaises(ClassifierTableInvalid):
            validate_rule_table(broken)

    def test_guard_2_rejects_a_table_with_no_constant_true_catch_all(self):
        with self.assertRaises(ClassifierTableInvalid):
            validate_rule_table(mutate(RULES, drop=(12,)))

    def test_guard_3_rejects_two_rules_naming_one_state(self):
        clash = mutate(RULES, replace={5: Rule(5, "ENDED_WITH_OPEN_OBLIGATION", False,
                                               frozenset({"F2"}), lambda s: s.facts["F2"],
                                               "x")})
        with self.assertRaises(ClassifierTableInvalid):
            validate_rule_table(clash)

    def test_guard_4_rejects_the_F_006_SHAPE(self):
        """The M1 mutant table cannot be written into production."""
        with self.assertRaises(ClassifierTableInvalid):
            validate_rule_table(m1_table())

    def test_guard_5_rejects_a_fail_closed_rule_demoted_below_an_actionable_one(self):
        with self.assertRaises(ClassifierTableInvalid):
            validate_rule_table(mutate(RULES, swap=(3, 11)))

    def test_guard_6_rejects_WAITING_ON_HUMAN_demoted_below_R11(self):
        """The M2 mutant table cannot be written into production."""
        with self.assertRaises(ClassifierTableInvalid):
            validate_rule_table(m2_table())

    def test_guard_7_rejects_R2_demoted_below_a_rule_that_consults_a_safety_fact(self):
        """CON-4 iteration 2: strictly stronger than guard 5, and neither subsumes it."""
        demoted = mutate(RULES, swap=(2, 7))
        with self.assertRaises(ClassifierTableInvalid):
            validate_rule_table(demoted)

    def test_guard_7_is_not_subsumed_by_guard_5(self):
        """A table that satisfies guard 5 and violates guard 7 must still be rejected.

        R2 moved below R7 keeps every fail-closed rule above every ACTIONABLE rule -- guard
        5 is satisfied -- while the harm is R7 DECLINING on an unknown F3, which happens
        above R11.  Guard 7 is what catches it.
        """
        demoted = mutate(RULES, swap=(2, 5))          # R2 -> position 5, still above R6
        actionable = min(rule.index for rule in demoted if rule.actionable)
        fail_closed = max(rule.index for rule in demoted
                          if rule.state in watchdog_classifier.FAIL_CLOSED_STATES)
        self.assertLess(fail_closed, actionable, "guard 5 is satisfied by this table")
        with self.assertRaises(ClassifierTableInvalid):
            validate_rule_table(demoted)


# ======================================================================================
# UR-3.  Three mutations, three witnesses, mutants EXECUTED and asserted KILLED.
# ======================================================================================
def m1_table():
    """M1 -- the F-006 mutation: scope ``runnable`` back inside the liveness disjunction."""
    mutant_r8 = Rule(8, "ACTIVE_DISPATCH_WAIT", False, frozenset({"F5", "F6"}),
                     lambda s: (s.facts["F5"] and s.facts["F6"]) or s.facts["F9"],
                     "MUTANT: runnable scoped back inside the liveness disjunction")
    return mutate(RULES, replace={8: mutant_r8}, drop=(10,))


def m2_table():
    """M2 -- breaks AC-2: swap R7 (WAITING_ON_HUMAN) and R11 (STALLED_RECOVERABLE)."""
    return mutate(RULES, swap=(7, 11))


def m3_table():
    """M3 -- breaks the O-2 boundary: move R9 below R11."""
    return mutate(RULES, swap=(9, 11))


class MutationSensitivityTests(unittest.TestCase):
    """Each mutant is BUILT and RUN.  Mutation sensitivity is demonstrated, not claimed."""

    def test_M1_is_killed_by_W1(self):
        witness = witness_w1()
        correct = classify(witness)
        self.assertEqual(correct.state, "ACTIVE_DISPATCH_WAIT")
        self.assertEqual(correct.rule_index, 8)
        mutant = classify(witness, rules=m1_table())
        self.assertEqual(mutant.state, "IDLE_UNCLASSIFIED_FAIL_CLOSED",
                         "M1 must be KILLED: a mutant that agrees with the correct table "
                         "on this witness proves the test is worthless")
        self.assertNotEqual(correct.state, mutant.state)

    def test_M2_is_killed_by_W2(self):
        witness = witness_w2()
        correct = classify(witness)
        self.assertEqual((correct.state, correct.rule_index), ("WAITING_ON_HUMAN", 7))
        mutant = classify(witness, rules=m2_table())
        self.assertEqual(mutant.state, "STALLED_RECOVERABLE",
                         "M2 must be KILLED; under it a WAITING_FOR_INPUT run is "
                         "auto-advanced, which is a direct AC-2 violation")
        self.assertNotEqual(correct.state, mutant.state)

    def test_M3_is_killed_by_W3(self):
        witness = witness_w3()
        correct = classify(witness)
        self.assertEqual((correct.state, correct.rule_index),
                         ("RECONCILIATION_OWED_TO_ENGINE", 9))
        mutant = classify(witness, rules=m3_table())
        self.assertEqual(mutant.state, "STALLED_RECOVERABLE",
                         "M3 must be KILLED; under it a run whose external effect may "
                         "already exist is re-driven, which is an AC-5 violation")
        self.assertNotEqual(correct.state, mutant.state)

    def test_the_anti_requirement_a_healthy_run_only_suite_would_NOT_satisfy_UR_3(self):
        """O-1's input cannot detect the mutations, which is exactly why W1..W3 exist.

        The approved DESIGN states that a healthy-run-only suite passes unchanged under
        ALL THREE mutants.  Against the table as built that holds for M1 and M3 and NOT
        for M2: a LITERAL swap of R7 and R11 -- the mutant DESIGN specifies -- lifts R11
        to position 7, above R8, so it perturbs the healthy input too.  The mutant is
        implemented as DESIGN specifies and the discrepancy is reported rather than
        papered over by reshaping the mutation; the anti-requirement's force is unchanged,
        because two of the three drifts that matter stay invisible on a healthy run and
        one detected mutant out of three is not UR-3.
        """
        healthy = make_snapshot(true_facts=("F5", "F6", "F9"))
        baseline = classify(healthy).state
        for name, table in (("M1", m1_table()), ("M3", m3_table())):
            self.assertEqual(classify(healthy, rules=table).state, baseline,
                             f"{name} is invisible on a healthy run -- which is exactly "
                             "why W1 and W3 exist")
        self.assertNotEqual(classify(healthy, rules=m2_table()).state, baseline,
                            "recorded as a DESIGN discrepancy, not silently accepted: a "
                            "literal R7<->R11 swap does move the healthy input")


class ConCorrectionWitnessTests(unittest.TestCase):
    """T-10 / T-10b: the ``ABSENT_DECLARED`` half and the ``UNSUPPORTED`` half of DI-3."""

    def test_T10_W1_classifies_ACTIVE_DISPATCH_WAIT_at_rule_index_8(self):
        witness = witness_w1()
        self.assertEqual(witness.status_authority, "declared_only")
        self.assertFalse(witness.facts["F11"],
                         "declared_only withdraws ONE contributor from TWO facts and "
                         "each keeps another, so no fact becomes uncovered")
        for fact in SAFETY_RELEVANT_FACTS:
            self.assertNotEqual(witness.support[fact], SUPPORT_UNSUPPORTED, fact)
        result = classify(witness)
        self.assertEqual((result.state, result.rule_index), ("ACTIVE_DISPATCH_WAIT", 8))

    def test_T10b_W4_every_safety_relevant_fact_uncovered_stops_at_rule_index_2(self):
        """Run once PER MEMBER, so a future narrowing of the set FAILS a test."""
        from scripts.deterministic_workflow.watchdog_observation import (
            observation_unsupported)
        self.assertEqual(len(SAFETY_RELEVANT_FACTS), 9)
        for fact in SAFETY_RELEVANT_FACTS:
            with self.subTest(uncovered=fact):
                witness = witness_w4(fact)
                self.assertTrue(observation_unsupported(witness.support))
                unsupported = make_snapshot(
                    true_facts=tuple(name for name, value in witness.facts.items()
                                     if value) + ("F11",),
                    support=dict(witness.support))
                result = classify(unsupported)
                self.assertEqual(result.rule_index, 2, fact)
                self.assertEqual(result.state, "UNSUPPORTED_FAIL_CLOSED")
                self.assertNotEqual(result.state, "STALLED_RECOVERABLE")

    def test_T10b_F3_and_F6_are_mandatory_members(self):
        for fact in ("F3", "F6"):
            self.assertIn(fact, SAFETY_RELEVANT_FACTS,
                          "AC-2 and AC-3 are Watchdog obligations and cannot be delegated "
                          "to a component that lacks the evidence for them")

    def test_T10b_the_paired_negative_the_SAME_fact_as_ABSENT_DECLARED_classifies_normally(self):
        """This is what proves the test pins the DISTINCTION, not a blanket block."""
        from scripts.deterministic_workflow.watchdog_observation import (
            observation_unsupported)
        for fact in SAFETY_RELEVANT_FACTS:
            with self.subTest(absent=fact):
                covered = make_snapshot(true_facts=("F5",), next_node="PREPARE_WORKER",
                                        support={fact: SUPPORT_ABSENT_DECLARED})
                self.assertFalse(observation_unsupported(covered.support))
                result = classify(covered)
                self.assertEqual((result.state, result.rule_index),
                                 ("STALLED_RECOVERABLE", 11))


class OverlapWalkthroughTests(unittest.TestCase):
    """UR-2: the six overlaps, each asserting the STATE and the RULE INDEX.

    Asserting the index is what makes a reordering that happens to keep the state right
    still fail.
    """

    def assert_overlap(self, snapshot, state, index):
        result = classify(snapshot)
        self.assertEqual((result.state, result.rule_index), (state, index))

    def test_O1_live_dispatch_foreign_lease_and_a_runnable_node_all_hold(self):
        self.assert_overlap(make_snapshot(true_facts=("F5", "F6", "F9")),
                            "ACTIVE_DISPATCH_WAIT", 8)

    def test_O2_reconciliation_outstanding_beside_a_runnable_node(self):
        self.assert_overlap(make_snapshot(true_facts=("F5", "F7")),
                            "RECONCILIATION_OWED_TO_ENGINE", 9)

    def test_O3_a_foreign_lease_beside_a_runnable_node(self):
        self.assert_overlap(make_snapshot(true_facts=("F5", "F9")),
                            "OWNED_ELSEWHERE_OBSERVE", 10)

    def test_O4_an_engine_refusal_outranks_everything_actionable(self):
        self.assert_overlap(make_snapshot(true_facts=("F5", "F6", "F10")),
                            "NOT_MINE_OBSERVE_ONLY", 3)

    def test_O5_a_terminal_run_with_an_open_delivery_obligation(self):
        """Deliberately DIVERGES from quiescence_verdict's ordering, and says why.

        The Watchdog answers "may I resume this RUN?", not "may this TURN end?", so
        terminal comes first with the obligation as a conjunct -- and neither classifier
        calls this input clean.
        """
        self.assert_overlap(make_snapshot(true_facts=("F2", "F8")),
                            "ENDED_WITH_OPEN_OBLIGATION", 4)
        from scripts.deterministic_workflow import quiescence
        self.assertNotEqual(quiescence.OBLIGATION_NONE,
                            quiescence.delivery_obligation({"delivery_state": "processed"}),
                            "the OS-44 classifier does not call this input clean either")

    def test_O6_an_expired_heartbeat_on_a_WAITING_FOR_INPUT_run_stays_WAITING(self):
        """AC-2 structurally: nothing above R7 reads a lease, so liveness cannot move it."""
        for liveness in ("EXPIRED", "LIVE", "ABSENT", "UNREADABLE"):
            with self.subTest(liveness=liveness):
                self.assert_overlap(
                    make_snapshot(true_facts=("F3", "F5"), liveness_status=liveness),
                    "WAITING_ON_HUMAN", 7)


class FailClosedRouteTests(unittest.TestCase):
    """R1/R2/R3 above everything actionable -- SAFE-5 and SAFE-6, executed."""

    def test_an_unreadable_authority_routes_to_R1_even_with_a_runnable_node(self):
        result = classify(make_snapshot(true_facts=("F1", "F5")))
        self.assertEqual((result.state, result.rule_index),
                         ("UNDECIDABLE_FAIL_CLOSED", 1))

    def test_SAFE_6_no_uncovered_fact_reaches_any_rule_that_could_consume_it(self):
        for fact in SAFETY_RELEVANT_FACTS:
            with self.subTest(uncovered=fact):
                snapshot = make_snapshot(
                    true_facts=("F5", "F11"), next_node="PREPARE_WORKER",
                    support={fact: SUPPORT_UNSUPPORTED})
                result = classify(snapshot)
                self.assertLessEqual(result.rule_index, 2)
                self.assertFalse(result.actionable)

    def test_R6_fires_only_on_the_engine_s_own_continuation_verdict(self):
        armed = make_snapshot(true_facts=("F3", "F4"),
                              pause_verdict=pause_policy.PAUSE_CONTINUATION_RECOVERABLE)
        result = classify(armed)
        self.assertEqual((result.state, result.rule_index),
                         ("PAUSE_CONTINUATION_RECOVERABLE", 6))
        # A pause the engine calls merely RESUMABLE is a human's open decision (NG-5).
        resumable = make_snapshot(true_facts=("F3", "F4"),
                                  pause_verdict=pause_policy.PAUSE_RESUMABLE)
        self.assertEqual(classify(resumable).state, "WAITING_ON_HUMAN")


class ImportClosureTests(unittest.TestCase):
    """CON-1 / T-6: the core cannot REACH routing, the graph, the executor or a claim."""

    CORE = ("scripts.deterministic_workflow.watchdog_classifier",
            "scripts.deterministic_workflow.watchdog_observation",
            "scripts.deterministic_workflow.watchdog_state",
            "scripts.deterministic_workflow.watchdog_supervisor")

    def closure(self) -> set[str]:
        import importlib
        import sys
        seen: set[str] = set()
        frontier = list(self.CORE)
        while frontier:
            name = frontier.pop()
            if name in seen:
                continue
            seen.add(name)
            module = sys.modules.get(name) or importlib.import_module(name)
            for value in vars(module).values():
                candidate = getattr(value, "__module__", None) or getattr(
                    value, "__name__", None)
                if isinstance(candidate, str) and candidate.startswith(
                        "scripts.deterministic_workflow"):
                    frontier.append(candidate)
        return seen

    def test_the_core_reaches_no_routing_graph_executor_or_decision_module(self):
        closure = self.closure()
        for forbidden in ("routing", "graph", "executor", "decision_gate", "pause_runtime",
                          "pause_store", "runtime_state", "recovery_runtime",
                          "recovery_store"):
            self.assertNotIn(f"scripts.deterministic_workflow.{forbidden}", closure,
                             f"the core reached {forbidden}; the only route into the "
                             "engine must be RecoveryInvocationPort")

    def test_the_core_binds_no_claim_takeover_resume_or_route_symbol(self):
        import importlib
        for name in self.CORE:
            module = importlib.import_module(name)
            for symbol in ("claim", "takeover", "resume_run", "route",
                           "recover_stalled_run"):
                self.assertNotIn(symbol, vars(module),
                                 f"{name} binds {symbol!r}")


# ======================================================================================
# TEST phase, gap G-7.  What ``ImportClosureTests`` above actually measures.
#
# ``ImportClosureTests.closure()`` walks the module-level BINDINGS of the four core
# modules -- ``vars(module)`` -- and never asks what was imported.  Both of the core's
# lazy imports are therefore invisible to it (``watchdog_state.backoff_delay`` ->
# ``lease_keeper``, ``watchdog_supervisor.run_continuous`` -> ``pause_store``), and so is
# every module reached only transitively.  Observed in a fresh interpreter, importing the
# four core modules already puts ``routing`` into ``sys.modules`` -- pulled in through
# ``ports``/``state``, not by the Watchdog -- so "the core's transitive import closure
# contains no routing module" is NOT what that test establishes, and the source comments
# that rest on it overstate their evidence.
#
# The property that is BOTH true and load-bearing is narrower and is asserted here
# instead: no module that can take a claim, open a checkpoint or perform an effect is
# loaded by the core, on the import path OR on either lazy path.  That is what makes
# `RecoveryInvocationPort` the only route into the engine, and it is checked in a fresh
# subprocess so nothing another suite already imported can satisfy it.
# ======================================================================================
_CLOSURE_PROBE = r"""
import importlib, json, sys
sys.path.insert(0, ".")
CORE = ("scripts.deterministic_workflow.watchdog_classifier",
        "scripts.deterministic_workflow.watchdog_observation",
        "scripts.deterministic_workflow.watchdog_state",
        "scripts.deterministic_workflow.watchdog_supervisor")
for name in CORE:
    importlib.import_module(name)


def loaded():
    return sorted(name.split(".")[-1] for name in sys.modules
                  if name.startswith("scripts.deterministic_workflow."))


on_import = loaded()
# The two LAZY paths, driven so their imports actually happen.
from scripts.deterministic_workflow import watchdog_state, watchdog_supervisor
watchdog_state.backoff_delay(1, lease_seconds=60.0)


class _Empty:
    def discover(self):
        return ()


try:
    watchdog_supervisor.run_continuous(discovery=_Empty(), observation=None,
                                       liveness=None, recovery=None, audit=None,
                                       max_sweeps=1)
except Exception:                                    # noqa: BLE001 - the ports are None
    pass
print("PROBE " + json.dumps({"on_import": on_import, "after_lazy_paths": loaded()}))
"""


class ActualImportClosureTests(unittest.TestCase):
    """CON-1 / UR-4, measured as ``sys.modules`` rather than as module attributes."""

    #: Every module that can take a run-scoped claim, open a checkpoint store, build or
    #: invoke the graph, or perform an external effect.  The core must reach NONE of them:
    #: `RecoveryInvocationPort` is the only route into the engine.
    EFFECT_AND_CLAIM_MODULES = ("graph", "executor", "pause_runtime", "recovery_runtime",
                                "recovery_store", "checkpoint_store", "orca_adapter",
                                "fake_adapter", "launcher", "turn_boundary")

    @classmethod
    def setUpClass(cls):
        import json
        import subprocess
        import sys
        from pathlib import Path
        completed = subprocess.run(
            [sys.executable, "-c", _CLOSURE_PROBE], capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parent.parent), check=False)
        if completed.returncode != 0:                # pragma: no cover - reported, never hidden
            raise AssertionError(f"the closure probe failed: {completed.stderr.strip()}")
        line = next(row for row in completed.stdout.splitlines()
                    if row.startswith("PROBE "))
        cls.observed = json.loads(line[len("PROBE "):])

    def test_importing_the_core_loads_no_claim_or_effect_module(self):
        for forbidden in self.EFFECT_AND_CLAIM_MODULES:
            self.assertNotIn(forbidden, self.observed["on_import"],
                             f"importing the Watchdog core loaded {forbidden}; the only "
                             "route into the engine must be RecoveryInvocationPort")

    def test_neither_LAZY_path_loads_one_either(self):
        """``backoff_delay`` -> ``lease_keeper`` and ``run_continuous`` -> ``pause_store``
        are the two imports the attribute walk cannot see, so they are driven here."""
        for forbidden in self.EFFECT_AND_CLAIM_MODULES:
            self.assertNotIn(forbidden, self.observed["after_lazy_paths"], forbidden)

    def test_the_lazy_paths_really_WERE_driven(self):
        """Otherwise the test above would be vacuous."""
        gained = set(self.observed["after_lazy_paths"]) - set(self.observed["on_import"])
        self.assertIn("lease_keeper", gained, "backoff_delay's import did not happen")
        self.assertIn("pause_store", gained, "run_continuous's import did not happen")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
