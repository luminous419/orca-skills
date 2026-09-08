"""OS-43 layer (a): the atomic observation-fact snapshot and its support vector.

This is half of the two-layer model UR-1 requires to stay separate, and it is its own
module for exactly that reason: layer (b) -- the ordered classifier -- is
:mod:`watchdog_classifier`, and neither may absorb the other.

Layer (a) answers "what is durably true about this run?" and NOTHING else.  It computes no
next node, no recoverability verdict, no review gate and no Responsible Phase: every fact
below is a TRANSCRIPTION of an authority that already exists, which is what makes CON-1 a
property of the code rather than a rule someone has to remember.

Four construction rules are load-bearing:

1. **One invocation, no re-read.**  Each :class:`ports.RunObservationPort` method is called
   at most once and the vector is built from what came back -- the shape
   ``turn_boundary.observe`` already has ("Everything the turn-end verdict needs, gathered
   in one atomic invocation", ``turn_boundary.py:619-624``).  Re-reading a fact
   mid-classification would break the determinism argument's operational precondition.
2. **Every fact is total.**  Each of F1..F11 has a defined value on every input, including
   unreadable evidence.
3. **F1 is the sink for every refusal.**  Any authority that exists and RAISES sets that
   fact's support to ``UNREADABLE`` *and* F1 to true.  The port never collapses absent into
   unreadable; the builder is where a refusal becomes a fact.
4. **The wait read is tri-valued and is NEW code.**  F3 must not reuse
   ``turn_boundary.observe_durable_wait``: its pause-record witness swallows
   ``OSError``/``ValueError`` and returns ``""`` (``turn_boundary.py:569-572``), its
   clarification witness swallows ``OSError`` (``:595-598``) and its gate witness swallows
   ``TurnBoundaryUnavailable`` (``:601-608``).  Each is safe for a turn end -- an empty
   result only refuses a turn -- and each is unsafe here, where it would read as "no human
   wait armed" and auto-resume one.
5. **Support is folded per fact, and ``ABSENT_DECLARED`` is not ``UNSUPPORTED``.**  An
   authority that answered "legitimately not present" leaves the fact COVERED and its value
   a KNOWN false; a fact no declared authority covers at all is UNKNOWN.  The fourth branch
   of the fold is an unconditional ``else``, which is what makes ``support`` total over
   :data:`FACT_IDS` -- the precondition :func:`observation_unsupported` relies on.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import contracts

FACT_IDS = ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11")

#: What each fact NAMES.  Carried in the source so an audit row and a reviewer read the
#: same thing, and so a future edit has to move the name with the rule.
FACT_NAMES = {
    "F1": "OBSERVATION_UNREADABLE",
    "F2": "TERMINAL_RECORDED",
    "F3": "DURABLE_HUMAN_WAIT_ARMED",
    "F4": "RECOVERABLE_PAUSE_ACTIONABLE",
    "F5": "RUNNABLE_NEXT_NODE",
    "F6": "ACTIVE_DISPATCH_CORROBORATED",
    "F7": "DISPATCH_RECONCILIATION_OUTSTANDING",
    "F8": "DELIVERY_OBLIGATION_OUTSTANDING",
    "F9": "RECOVERY_LEASE_LIVE",
    "F10": "OWNERSHIP_CONFLICT_REPORTED",
    "F11": "OBSERVATION_UNSUPPORTED",
}

SUPPORT_SUPPORTED = "SUPPORTED"              # a declared authority answered
SUPPORT_ABSENT_DECLARED = "ABSENT_DECLARED"  # verifiably absent; the fact stays COVERED
SUPPORT_UNSUPPORTED = "UNSUPPORTED"          # no declared authority covers this fact
SUPPORT_UNREADABLE = "UNREADABLE"            # an authority exists and raised => F1
SUPPORT_VALUES = (SUPPORT_SUPPORTED, SUPPORT_ABSENT_DECLARED, SUPPORT_UNSUPPORTED,
                  SUPPORT_UNREADABLE)

#: The facts whose TRUTH is a precondition of an actionable state (R11 needs F5; R6 needs
#: F4's verdict).  Retained for exposition and for ``validate_rule_table`` guard 4.  It is
#: NOT the domain of the F11 predicate -- see :data:`SAFETY_RELEVANT_FACTS`.
ENABLING_FACTS = ("F4", "F5")

#: Every fact on which an actionable classification depends in EITHER direction: the two
#: enablers above and the seven vetoes.  F11 is set by an ``UNSUPPORTED`` member of THIS
#: set, because CON-4 forbids treating an observation that no declared authority covers as
#: either a success or a licence -- an uncovered VETO read as False is a guess in the
#: success direction exactly as much as an uncovered ENABLER read as True.
#: ``ABSENT_DECLARED`` is not a member of that hazard and never sets F11.
#:
#: F1 is excluded because its support is COMPUTED from the other facts' support vector
#: rather than read from an authority, so ``UNSUPPORTED`` is unreachable for it -- and R1
#: precedes R2 anyway.  F11 is excluded because it is this predicate's own output and
#: including it would be self-referential.
SAFETY_RELEVANT_FACTS = ("F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10")

#: Which authorities contribute to which fact.  Stated once, here, where the facts are.
FACT_CONTRIBUTORS: dict[str, tuple[str, ...]] = {
    "F2": ("checkpoint", "pause"),
    "F3": ("durable_wait",),
    "F4": ("pause",),
    "F5": ("checkpoint", "orca"),
    "F6": ("orca",),
    "F7": ("orca",),
    "F8": ("deliveries",),
    "F9": ("foreign_lease",),
    "F10": ("watchdog_ledger",),
}

TERMINAL_RUN_STATUSES = frozenset({"COMPLETED", "BLOCKED", "ESCALATED", "CANCELLED",
                                   "ABANDONED"})
#: ``turn_boundary``'s own action prefixes, transcribed rather than respelled.
ACTION_RECONCILE_DISPATCH = "reconcile_dispatch"


class ObservationUnavailable(RuntimeError):
    """An authority for this fact EXISTS and could not be read.

    Deliberately not a value: "the fact is false" and "I could not find out whether the
    fact is true" are different, and collapsing the second into the first is the fail-open
    shape OS-43 exists to remove.  It routes to F1 and therefore to R1.
    """


class ObservationUnsupported(RuntimeError):
    """NO declared authority covers this fact for this runtime at all.

    Distinct from :class:`ObservationUnavailable`: nothing raised, because nothing was ever
    asked.  It routes to F11 and therefore to R2 whenever the fact is safety-relevant.
    """


@dataclass(frozen=True)
class ObservationSnapshot:
    """ONE atomic read.  Frozen, because re-reading a fact mid-classification would break
    the determinism argument's operational precondition."""

    run_id: str
    observed_at: str
    facts: Mapping[str, bool]                 # total over FACT_IDS
    support: Mapping[str, str]                # total over FACT_IDS, in SUPPORT_VALUES
    evidence: Mapping[str, tuple[str, ...]]   # per fact, the authority ids that answered
    pause_verdict: str = ""                   # F4's carried verdict; "" when F4 is false
    next_node: str = ""                       # F5's carried route token; "" when false
    thread_id: str = ""
    checkpoint_ns: str = ""
    head_checkpoint_id: str = ""
    status_authority: str = ""                # turn_boundary.STATUS_AUTHORITY_* verbatim
    liveness_status: str = ""                 # coordinator_liveness.LIVENESS_* verbatim
    snapshot_digest: str = ""

    def __post_init__(self) -> None:
        if set(self.facts) != set(FACT_IDS):
            raise ValueError(f"facts must be total over {FACT_IDS}")
        if set(self.support) != set(FACT_IDS):
            raise ValueError(f"support must be total over {FACT_IDS}")
        for fact, value in self.support.items():
            if value not in SUPPORT_VALUES:
                raise ValueError(f"{fact}: unknown support value {value!r}")
        if self.support["F1"] != SUPPORT_SUPPORTED:
            raise ValueError(
                "F1's support is computed from the other facts' support vector, so it is "
                "SUPPORTED by construction; UNSUPPORTED is not a reachable value for it")


def observation_unsupported(support: Mapping[str, str]) -> bool:
    """F11.  True iff SOME SAFETY-RELEVANT fact is UNCOVERED.

    Uncovered means no declared authority covers it at all, so its value is unknown rather
    than false.

    CON-4.  An uncovered VETO is fail-closed for the same reason as an uncovered ENABLER:
    reading F3 as "no human wait is armed" or F6 as "no agent is running" because nothing
    could answer is a guess in the success direction.  It is NOT cured by the engine --
    ``classify_head`` reasons from the head pointer and its stored parent links only
    (``pause_runtime.py:460-461``) and can see neither a durable wait artefact nor an Orca
    worker listing, so it cannot reconstruct AC-2's or AC-3's veto.  This is also the
    repository's own rule at this boundary: "a source that cannot be read is 'unknown',
    never 'empty': raise rather than return a short tuple" (``ports.py:141-148``), and
    "reading a corrupt or incompatible file as 'no prior claim'" is refused
    (``runtime_state.py:125-130``).

    ``ABSENT_DECLARED`` is NOT ``UNSUPPORTED`` and never sets F11: an authority was
    consulted and answered that the thing is legitimately not there.  ``status_authority ==
    "declared_only"`` (``turn_boundary.py:645-652``, ``:669-675``) withdraws the CHECKPOINT
    contributor of F2 and F5 and nothing else; F5's Orca contributor stays ``SUPPORTED``
    (``turn_boundary.py:352-360``) and F2's pause-record contributor still answers
    (``pause_store.py:515-519``), so both stay COVERED and F11 is NOT set for a
    prompt-driven run.
    """
    return any(support[fact] == SUPPORT_UNSUPPORTED for fact in SAFETY_RELEVANT_FACTS)


@dataclass(frozen=True)
class _Answer:
    """What ONE authority said about itself: covered and answering, covered and absent,
    uncovered, or present-and-unreadable."""

    support: str
    payload: Any = None

    @property
    def readable(self) -> bool:
        return self.support in (SUPPORT_SUPPORTED, SUPPORT_ABSENT_DECLARED)


def _ask(call: Any, run_id: str, *, absent: Any = None) -> _Answer:
    """Consult one authority once, and classify HOW it answered -- never WHAT it means."""
    try:
        value = call(run_id)
    except ObservationUnsupported:
        return _Answer(SUPPORT_UNSUPPORTED)
    except ObservationUnavailable:
        return _Answer(SUPPORT_UNREADABLE)
    if value is absent or value == absent:
        return _Answer(SUPPORT_ABSENT_DECLARED, value)
    return _Answer(SUPPORT_SUPPORTED, value)


def fold_support(contributors: tuple[str, ...]) -> str:
    """The four-branch fold, total, in this fixed order.

    1. any contributor ``UNREADABLE``  => ``UNREADABLE``  (=> F1), never "false".
    2. else any ``SUPPORTED``          => ``SUPPORTED``.
    3. else every covering contributor answered "absent" => ``ABSENT_DECLARED``.
    4. else -- every contributor is uncovered, or there is no contributor at all --
       => ``UNSUPPORTED``.  The empty-contributor case is UNSUPPORTED, never
       ABSENT_DECLARED: nothing answered, so nothing is known.
    """
    if SUPPORT_UNREADABLE in contributors:
        return SUPPORT_UNREADABLE
    if SUPPORT_SUPPORTED in contributors:
        return SUPPORT_SUPPORTED
    if contributors and all(value == SUPPORT_ABSENT_DECLARED for value in contributors):
        return SUPPORT_ABSENT_DECLARED
    return SUPPORT_UNSUPPORTED


def snapshot(run_id: str, *, observation: Any, liveness: Any = None, clock: Any = None,
             ledger: Mapping[str, Mapping[str, Any]] | None = None,
             ledger_unreadable: bool = False,
             observed_at: str = "") -> ObservationSnapshot:
    """Build ONE frozen fact vector for ONE run.

    ``ledger`` is the Watchdog's own folded ledger -- F10's authority, and the only one
    that is always present because the Watchdog writes it.  ``None`` means the ledger was
    not consulted at all, which is ``UNSUPPORTED`` for F10 and therefore R2: a statement
    about *this* Watchdog's authority is itself an observation, and one nothing covers is
    not "no conflict".

    ``ledger_unreadable`` is the OTHER failure and is deliberately a different one: the
    ledger EXISTS and its fold refused.  That is ``UNREADABLE``, so it routes to F1 and
    therefore to R1 rather than to R2 -- an authority that exists and raised names a
    repairable cause, while one nothing covers names a missing declaration, and the two
    escalate differently.
    """
    orca = _ask(observation.orca_state, run_id)
    checkpoint = _ask(observation.checkpoint_state, run_id)
    pause = _ask(observation.pause_state, run_id)
    wait = _ask(observation.durable_wait, run_id)
    deliveries = _ask(observation.delivery_obligations, run_id, absent=())
    foreign = _ask(observation.foreign_lease, run_id)
    capabilities = _ask(observation.declared_capabilities, run_id, absent=frozenset())

    # A checkpoint the authority reports as legitimately not present is ABSENT_DECLARED --
    # "a real and normal case" (``turn_boundary.py:472-474``) -- and never UNSUPPORTED.
    if checkpoint.support == SUPPORT_SUPPORTED and \
            not (checkpoint.payload or {}).get("present", False):
        checkpoint = _Answer(SUPPORT_ABSENT_DECLARED, dict(checkpoint.payload or {}))
    if wait.support == SUPPORT_SUPPORTED and not (wait.payload or {}).get("evidence"):
        wait = _Answer(SUPPORT_ABSENT_DECLARED, dict(wait.payload or {}))
    if foreign.support == SUPPORT_SUPPORTED and foreign.payload is None:
        foreign = _Answer(SUPPORT_ABSENT_DECLARED)

    contributor_support = {
        "checkpoint": checkpoint.support, "pause": pause.support,
        "durable_wait": wait.support, "orca": orca.support,
        "deliveries": deliveries.support, "foreign_lease": foreign.support,
        "watchdog_ledger": (SUPPORT_UNREADABLE if ledger_unreadable
                            else SUPPORT_UNSUPPORTED if ledger is None
                            else SUPPORT_SUPPORTED),
    }
    support: dict[str, str] = {}
    evidence: dict[str, tuple[str, ...]] = {}
    for fact, names in FACT_CONTRIBUTORS.items():
        support[fact] = fold_support(tuple(contributor_support[name] for name in names))
        evidence[fact] = tuple(name for name in names
                               if contributor_support[name] in (SUPPORT_SUPPORTED,
                                                                SUPPORT_ABSENT_DECLARED))

    # ---- the values, each a transcription of what the authority returned --------------
    checkpoint_payload = dict(checkpoint.payload or {}) if checkpoint.readable else {}
    orca_payload = dict(orca.payload or {}) if orca.readable else {}
    pause_payload = dict(pause.payload or {}) if (pause.readable and pause.payload) else {}
    wait_payload = dict(wait.payload or {}) if wait.readable else {}

    checkpoint_status = str(checkpoint_payload.get("run_status") or "")
    pause_status = str(pause_payload.get("status") or "")
    next_node = str(checkpoint_payload.get("next_node") or "")
    runnable_actions = tuple(orca_payload.get("runnable_actions") or ())
    active_dispatches = tuple(orca_payload.get("active_dispatches") or ())
    dispatch_actions = tuple(action for action in runnable_actions
                             if not action.startswith(f"{ACTION_RECONCILE_DISPATCH}:"))

    facts: dict[str, bool] = {}
    facts["F2"] = (checkpoint_status in TERMINAL_RUN_STATUSES
                   or pause_status in ("CANCELLED", "ABANDONED"))
    facts["F3"] = bool(wait_payload.get("evidence"))
    facts["F4"] = bool(pause_payload.get("verdict"))
    facts["F5"] = bool(next_node) or bool(dispatch_actions)
    facts["F6"] = bool(active_dispatches)
    facts["F7"] = any(action.startswith(f"{ACTION_RECONCILE_DISPATCH}:")
                      for action in runnable_actions)
    facts["F8"] = bool(deliveries.payload) if deliveries.readable else False
    facts["F9"] = bool(foreign.payload) if foreign.readable else False
    head = str(checkpoint_payload.get("head_checkpoint_id")
               or pause_payload.get("checkpoint_id") or "")
    facts["F10"] = _refusal_reported(ledger, head)

    # ---- rule 3: F1 is the sink for every refusal ------------------------------------
    liveness_status = ""
    liveness_unreadable = False
    if liveness is not None:
        try:
            liveness_status = str(liveness.status(run_id))
        except (ObservationUnavailable, ObservationUnsupported):
            liveness_unreadable = True
    unreadable_facts = [fact for fact, value in support.items()
                        if value == SUPPORT_UNREADABLE]
    # A wait witness that is itself unreadable is F1 too, even when the other two answered
    # -- ``durable_wait`` reports it rather than swallowing it (construction rule 4).
    wait_unreadable = bool(wait_payload.get("unreadable"))
    facts["F1"] = bool(unreadable_facts) or wait_unreadable or liveness_unreadable
    support["F1"] = SUPPORT_SUPPORTED
    evidence["F1"] = tuple(sorted(unreadable_facts))

    # ---- F11, per D-0/DI-3 -----------------------------------------------------------
    support["F11"] = SUPPORT_SUPPORTED
    undeclared = _undeclared_capability(capabilities,
                                        needs_external_resume=facts["F7"])
    facts["F11"] = observation_unsupported(support) or undeclared
    evidence["F11"] = tuple(sorted(fact for fact in SAFETY_RELEVANT_FACTS
                                   if support[fact] == SUPPORT_UNSUPPORTED))

    digest = contracts.stable_id("watchdog_snapshot", {
        "run_id": run_id, "facts": {key: bool(facts[key]) for key in FACT_IDS},
        "support": {key: support[key] for key in FACT_IDS}})
    return ObservationSnapshot(
        run_id=run_id, observed_at=observed_at or _stamp(clock),
        facts={key: bool(facts[key]) for key in FACT_IDS},
        support={key: support[key] for key in FACT_IDS},
        evidence={key: evidence.get(key, ()) for key in FACT_IDS},
        pause_verdict=str(pause_payload.get("verdict") or ""),
        next_node=next_node or (dispatch_actions[0] if dispatch_actions else ""),
        thread_id=str(checkpoint_payload.get("thread_id") or
                      pause_payload.get("thread_id") or ""),
        checkpoint_ns=str(checkpoint_payload.get("checkpoint_ns") or
                          pause_payload.get("checkpoint_ns") or ""),
        head_checkpoint_id=head,
        status_authority=str(checkpoint_payload.get("status_authority") or
                             _status_authority(checkpoint, pause_status)),
        liveness_status=liveness_status, snapshot_digest=digest)


def _refusal_reported(ledger: Mapping[str, Mapping[str, Any]] | None, head: str) -> bool:
    """F10.  Has the ENGINE already refused this Watchdog on THIS head?

    Head-keyed, so a run that genuinely advances is legitimately reconsidered while one
    that has not moved is not -- the same property the recovery identity has, reached
    without importing the engine into the core.
    """
    if not ledger:
        return False
    for row in ledger.values():
        if row.get("last_outcome") == "CONFLICT" and str(row.get("head_before") or "") \
                == head:
            return True
    return False


def _undeclared_capability(capabilities: _Answer, *, needs_external_resume: bool) -> bool:
    """F11's second producer: a runtime capability nothing declares, ON A PATH THAT NEEDS IT.

    Unchanged from the approved model and additive to the uncovered-fact clause -- F11 is
    the disjunction of the two.  The scoping matters and is deliberate: the real
    ``OrcaAdapter`` withholds ``external_resume`` permanently and on purpose
    (``orca_adapter.py:64-71``), so reading its absence as F11 unconditionally would make
    the Watchdog inert on every real run without removing any hazard.  It is a hazard
    exactly where ``executor._recover`` would need it -- an already-dispatched effect that
    must be re-collected, which is the F7 shape (``executor.py:185-189``).

    An authority that could not be asked at all is F11 outright; one that RAISED is F1's
    business, not this predicate's.
    """
    if capabilities.support == SUPPORT_UNSUPPORTED:
        return True
    if capabilities.support == SUPPORT_UNREADABLE:
        return False          # unreadable is F1's business, not F11's
    declared = frozenset(capabilities.payload or frozenset())
    return needs_external_resume and "external_resume" not in declared


#: Transcribed VERBATIM from ``turn_boundary``.  Kept as literals rather than an import so
#: the core's transitive import closure stays free of the boundary module; a contract test
#: asserts these three equal ``turn_boundary``'s own constants, so they cannot drift.
STATUS_AUTHORITY_CHECKPOINT = "workflow_checkpoint"
STATUS_AUTHORITY_PAUSE_RECORD = "os31_pause_record"
STATUS_AUTHORITY_DECLARED = "declared_only"


def _status_authority(checkpoint: _Answer, pause_status: str) -> str:
    if checkpoint.support == SUPPORT_SUPPORTED:
        return STATUS_AUTHORITY_CHECKPOINT
    if pause_status:
        return STATUS_AUTHORITY_PAUSE_RECORD
    return STATUS_AUTHORITY_DECLARED


def _stamp(clock: Any) -> str:
    if clock is not None and hasattr(clock, "now"):
        return str(clock.now())
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
