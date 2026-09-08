"""OS-43 layer (b): the ordered R1..R12 classifier.  Pure: no I/O, no clock, no claim.

The order IS the contract, exactly as ``quiescence.quiescence_verdict`` says of its own
("The order of the checks is the contract", ``quiescence.py:125-192``) and ``routing.route``
of its ("the sole workflow routing decision, evaluated in strict fail-closed order",
``routing.py:140``).  The rules are **not** mutually exclusive and nothing here assumes they
are: observation facts co-occur, the fact vector is a total ``Mapping[str, bool]`` over all
eleven rather than a tagged union, and R12's predicate is the constant ``True`` so
evaluation always terminates.

**The UR-3 rule, stated once in prose so it can be checked by reading.**  The liveness
disjunction is SPLIT into two rules that yield two different final states -- R8
(``F6`` -> ``ACTIVE_DISPATCH_WAIT``) and R10 (``F9`` -> ``OWNED_ELSEWHERE_OBSERVE``) -- and
the ``runnable`` conjunct is borne by exactly ONE rule, R11 (``F5`` ->
``STALLED_RECOVERABLE``), positioned strictly below both.  R11's written predicate is the
bare ``F5``; the guard that actually holds when it is REACHED is
``F5 ∧ ¬F1 ∧ ¬F11 ∧ ¬F10 ∧ ¬F2 ∧ ¬F3 ∧ ¬F6 ∧ ¬F7 ∧ ¬F8 ∧ ¬F9``, supplied by the ordering.
The correctness of R11 is POSITIONAL, not predicate-local, and this module says so rather
than claiming order-independence.  :func:`validate_rule_table` runs over :data:`RULES` at
import, so the F-006 shape is **unwritable** in production rather than merely fixed --
while ``classify``'s injectable ``rules`` keeps the mutants constructible in a test.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .watchdog_observation import SAFETY_RELEVANT_FACTS, ObservationSnapshot

#: Transcribed VERBATIM from ``pause_policy.PAUSE_CONTINUATION_RECOVERABLE``.  Kept as a
#: literal rather than an import ON PURPOSE: ``pause_policy`` imports ``routing`` at module
#: level, and CON-1 requires the Watchdog core's transitive import closure to contain no
#: routing module at all.  A contract test asserts this equals ``pause_policy``'s own
#: constant, so the two cannot drift.
PAUSE_VERDICT_CONTINUATION_RECOVERABLE = "PAUSE_CONTINUATION_RECOVERABLE"

# ---- the twelve states ----------------------------------------------------------------
UNDECIDABLE_FAIL_CLOSED = "UNDECIDABLE_FAIL_CLOSED"
UNSUPPORTED_FAIL_CLOSED = "UNSUPPORTED_FAIL_CLOSED"
NOT_MINE_OBSERVE_ONLY = "NOT_MINE_OBSERVE_ONLY"
ENDED_WITH_OPEN_OBLIGATION = "ENDED_WITH_OPEN_OBLIGATION"
ENDED = "ENDED"
PAUSE_CONTINUATION_RECOVERABLE = "PAUSE_CONTINUATION_RECOVERABLE"
WAITING_ON_HUMAN = "WAITING_ON_HUMAN"
ACTIVE_DISPATCH_WAIT = "ACTIVE_DISPATCH_WAIT"
RECONCILIATION_OWED_TO_ENGINE = "RECONCILIATION_OWED_TO_ENGINE"
OWNED_ELSEWHERE_OBSERVE = "OWNED_ELSEWHERE_OBSERVE"
STALLED_RECOVERABLE = "STALLED_RECOVERABLE"
IDLE_UNCLASSIFIED_FAIL_CLOSED = "IDLE_UNCLASSIFIED_FAIL_CLOSED"

#: R11 and R6, and nothing else.  The other ten states are observe-and-report.
ACTIONABLE_STATES = frozenset({STALLED_RECOVERABLE, PAUSE_CONTINUATION_RECOVERABLE})
#: R1, R2, R3 exactly.  ``IDLE_UNCLASSIFIED_FAIL_CLOSED`` is deliberately NOT a member: it
#: is the terminal catch-all, not one of the three refusals that must PRECEDE every
#: actionable rule, and including it would make guard 5 unsatisfiable.
FAIL_CLOSED_STATES = frozenset({UNDECIDABLE_FAIL_CLOSED, UNSUPPORTED_FAIL_CLOSED,
                                NOT_MINE_OBSERVE_ONLY})


class ClassifierTableInvalid(ValueError):
    """The rule table carries a shape the ordering guarantees forbid."""


@dataclass(frozen=True)
class Rule:
    index: int                       # 1..12; equals its position, asserted at import
    state: str
    actionable: bool
    reads: frozenset[str]            # the fact ids this rule's predicate may consult
    predicate: Callable[[ObservationSnapshot], bool]
    rationale: str                   # the per-position reason, carried into the source


@dataclass(frozen=True)
class Classification:
    state: str
    rule_index: int                  # reported, so a reorder that keeps the state still fails
    actionable: bool
    facts: Mapping[str, bool]
    snapshot_digest: str = ""


def _ALWAYS(snapshot: ObservationSnapshot) -> bool:
    """R12's predicate: the constant True.  This is what makes the table TOTAL."""
    return True


RULES: tuple[Rule, ...] = (
    Rule(1, UNDECIDABLE_FAIL_CLOSED, False, frozenset({"F1"}),
         lambda s: s.facts["F1"],
         "An authority that exists and raised names a repairable cause; it is strictly "
         "more informative than an uncovered fact, so it is read first."),
    Rule(2, UNSUPPORTED_FAIL_CLOSED, False, frozenset({"F11"}),
         lambda s: s.facts["F11"],
         "CON-4.  A safety-relevant fact no declared authority covers is UNKNOWN, not "
         "false; it must not reach any rule that could consume it, in either direction."),
    Rule(3, NOT_MINE_OBSERVE_ONLY, False, frozenset({"F10"}),
         lambda s: s.facts["F10"],
         "AC-7.  A refusal the engine has already issued against this Watchdog is not "
         "re-litigated by reclassifying the run."),
    Rule(4, ENDED_WITH_OPEN_OBLIGATION, False, frozenset({"F2", "F7", "F8"}),
         lambda s: s.facts["F2"] and (s.facts["F7"] or s.facts["F8"]),
         "Terminal comes first because the question is 'may I resume this RUN?', not "
         "'may this TURN end?'; the obligation is a conjunct, not a precedence."),
    Rule(5, ENDED, False, frozenset({"F2"}),
         lambda s: s.facts["F2"],
         "An ended run may never be resumed (SC-3)."),
    Rule(6, PAUSE_CONTINUATION_RECOVERABLE, True, frozenset({"F3", "F4"}),
         lambda s: (s.facts["F3"] and s.facts["F4"]
                    and s.pause_verdict == PAUSE_VERDICT_CONTINUATION_RECOVERABLE),
         "The ONE paused state a machine may continue: the engine already proved the "
         "head carries this bundle's own committed continuation, so no decision bundle "
         "is read and no human answer is substituted (NG-5)."),
    Rule(7, WAITING_ON_HUMAN, False, frozenset({"F3"}),
         lambda s: s.facts["F3"],
         "AC-2.  Above every rule that reads a lease, a dispatch or a next node, so no "
         "liveness signal can turn a human wait into recoverable work."),
    Rule(8, ACTIVE_DISPATCH_WAIT, False, frozenset({"F6"}),
         lambda s: s.facts["F6"],
         "AC-3.  Both authorities agree a dispatch is live; a run with work in flight is "
         "not stalled, whatever else is true of it."),
    Rule(9, RECONCILIATION_OWED_TO_ENGINE, False, frozenset({"F7", "F8"}),
         lambda s: s.facts["F7"] or s.facts["F8"],
         "AC-5, the O-2 boundary.  An external effect that may already exist is the "
         "engine's to reconcile; re-driving it is how a duplicate Dispatch is created."),
    Rule(10, OWNED_ELSEWHERE_OBSERVE, False, frozenset({"F9"}),
         lambda s: s.facts["F9"],
         "SC-6, and an OPTIMISATION only: it avoids a doomed claim.  The single-winner "
         "guarantee is the engine's atomic claim, never this rule."),
    Rule(11, STALLED_RECOVERABLE, True, frozenset({"F5"}),
         lambda s: s.facts["F5"],
         "AC-1's other three conjuncts.  Reached only when nine negations hold, each of "
         "them a COVERED false; the expired Coordinator lease is the action gate's."),
    Rule(12, IDLE_UNCLASSIFIED_FAIL_CLOSED, False, frozenset(),
         _ALWAYS,
         "TOTALITY.  Nothing above matched, so nothing is known to be actionable."),
)


def validate_rule_table(rules: tuple[Rule, ...]) -> None:
    """Every ordering guarantee the design rests on, asserted rather than remembered."""
    def refuse(message: str) -> None:
        raise ClassifierTableInvalid(message)

    # 1. positions are the contract
    if tuple(rule.index for rule in rules) != tuple(range(1, len(rules) + 1)):
        refuse("rule indices must be 1..n in order; a rule's index IS its position")
    # 2. TOTALITY: the last rule is the constant-true catch-all
    if not rules or rules[-1].predicate is not _ALWAYS or rules[-1].reads:
        refuse("the last rule must be the constant-true catch-all that reads no fact")
    # 3. DETERMINISM's premise: the named states are pairwise distinct
    if len({rule.state for rule in rules}) != len(rules):
        refuse("two rules name the same state; a state must identify its rule")
    # 4. UR-3, STRUCTURAL: `runnable` is borne by exactly ONE rule.  This is what makes
    #    the F-006 shape unwritable rather than merely fixed -- there is no disjunction
    #    for F5 to be scoped incorrectly across, and neither liveness arm names
    #    STALLED_RECOVERABLE.
    if sum(1 for rule in rules if "F5" in rule.reads) != 1:
        refuse("F5 (runnable) must be borne by exactly one rule; a liveness disjunction "
               "that also reads F5 is the F-006 shape")
    liveness_states = {rule.state for rule in rules
                       if rule.reads in (frozenset({"F6"}), frozenset({"F9"}))}
    if liveness_states != {ACTIVE_DISPATCH_WAIT, OWNED_ELSEWHERE_OBSERVE}:
        refuse("the liveness disjunction must be SPLIT into two rules yielding two "
               f"different states, got {sorted(liveness_states)}")
    # 5. SAFE-5 / AC-7: the three fail-closed rules precede every actionable rule
    actionable = [rule.index for rule in rules if rule.actionable]
    fail_closed = [rule.index for rule in rules if rule.state in FAIL_CLOSED_STATES]
    if not actionable or not fail_closed or max(fail_closed) >= min(actionable):
        refuse("every fail-closed rule must precede every actionable rule")
    if {rule.state for rule in rules if rule.actionable} != ACTIONABLE_STATES:
        refuse(f"the actionable states are exactly {sorted(ACTIONABLE_STATES)}")
    # 6. SAFE-1 / AC-2: R7 precedes every rule that reads a lease, a dispatch or a next node
    waiting = [rule.index for rule in rules if rule.state == WAITING_ON_HUMAN]
    consumers = [rule.index for rule in rules
                 if rule.reads & {"F5", "F6", "F9"}]
    if not waiting or (consumers and waiting[0] >= min(consumers)):
        refuse("WAITING_ON_HUMAN must precede every rule reading F5, F6 or F9")
    # 7. CON-4 / SAFE-6: the UNSUPPORTED route precedes every rule that CONSULTS a
    #    safety-relevant fact -- strictly stronger than guard 5, which only requires it to
    #    precede the ACTIONABLE rules.  The harm an uncovered fact causes is a VETO rule
    #    silently declining (R7 on an unknown F3, R8 on an unknown F6), and that happens
    #    above R11, so "before the actionable rules" is not enough.  Both guards are kept;
    #    neither subsumes the other.
    unsupported = [rule.index for rule in rules if rule.state == UNSUPPORTED_FAIL_CLOSED]
    safety_consumers = [rule.index for rule in rules
                        if rule.reads & set(SAFETY_RELEVANT_FACTS)]
    if not unsupported or (safety_consumers and unsupported[0] >= min(safety_consumers)):
        refuse("UNSUPPORTED_FAIL_CLOSED must precede every rule that consults a "
               "safety-relevant fact, not merely every actionable rule")


validate_rule_table(RULES)


def classify(snapshot: ObservationSnapshot, *,
             rules: tuple[Rule, ...] = RULES) -> Classification:
    """First-match-wins over ``rules`` in index order.  Pure.

    ``rules`` is injectable, and that is how UR-3's mutations become EXECUTABLE: a test
    builds a mutant tuple and calls ``classify(witness, rules=mutant)``.  This function
    deliberately does NOT call :func:`validate_rule_table` on an injected table -- a mutant
    must be constructible in a test while the production table stays guarded at import.
    """
    for rule in rules:
        if rule.predicate(snapshot):
            return Classification(rule.state, rule.index, rule.actionable,
                                  dict(snapshot.facts), snapshot.snapshot_digest)
    # Unreachable for any admissible table (guard 2), and never a silent fall-through.
    raise ClassifierTableInvalid(
        "no rule matched and the table has no constant-true catch-all; a classifier that "
        "can decline to answer is not total")


def rule_for(state: str, *, rules: tuple[Rule, ...] = RULES) -> Rule:
    for rule in rules:
        if rule.state == state:
            return rule
    raise KeyError(state)


def mutate(rules: tuple[Rule, ...], *, replace: Mapping[int, Rule] | None = None,
           drop: frozenset[int] | tuple[int, ...] = (),
           swap: tuple[int, int] | None = None) -> tuple[Rule, ...]:
    """Build a MUTANT table for a test.  Never used by production code.

    Re-indexes the survivors so the mutant is a well-formed first-match-wins table -- the
    mutation under test is the ORDER or the PREDICATE, never a malformed index sequence
    that would fail for the wrong reason.
    """
    rows = list(rules)
    if swap is not None:
        first, second = swap
        i, j = first - 1, second - 1
        rows[i], rows[j] = rows[j], rows[i]
    if replace:
        for index, rule in replace.items():
            rows[index - 1] = rule
    dropped = set(drop)
    rows = [rule for rule in rows if rule.index not in dropped]
    return tuple(
        Rule(position, rule.state, rule.actionable, rule.reads, rule.predicate,
             rule.rationale)
        for position, rule in enumerate(rows, start=1))
