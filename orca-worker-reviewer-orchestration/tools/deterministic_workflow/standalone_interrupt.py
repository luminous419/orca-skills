"""OS-37 N8.  The interrupt ladder, with identity re-verification as a HARD GATE.

    G0  assert_may_act(record, "signal")        <- the seven never-touch obligations
    G1  IDENTITY RE-VERIFICATION #1  --fail-->  "not_owned"          x no signal
    r0  optional in-band graceful hint          <- never evidence of death
    r1  GRACEFUL: SIGTERM, group-scoped only where ownership is provable
    r2  BOUNDED WAIT, re-checking ownership AND mode AT FIRE TIME
    G2  IDENTITY RE-VERIFICATION #2  --fail-->  "exit_unproven"      (rung 1 delivered)
                                     --fail-->  "not_owned"          (nothing delivered)
    r3  FORCE: SIGKILL, group-scoped only where provable
    G3  IDENTITY RE-VERIFICATION #3  --fail-->  "exit_unproven"      x no settle
    r4  PROOF OF DEATH: bounded wait for an OS-confirmed exit
          confirmed      --> "terminated_forced"
          NOT confirmed  --> "exit_unproven"  --> LOST, never COMPLETED/FAILED

The re-verification between rungs is not belt-and-braces.  Between rung 1 and rung 3 there
is a bounded wait, and a bounded wait is exactly long enough for the pid to be reaped and
recycled by an unrelated process.  Escalating on a remembered pid is how a supervisor
SIGKILLs a stranger, so ownership is re-derived from the process table at every rung and the
escalation is CANCELLED -- not retried -- if it changed.

**An unverified process is never reported as terminated.**  The mapping at rung 4 is total
and has no success branch for an unproven exit: ``exit_unproven`` routes to ``LOST`` with
``lost_reason="stop_unverified"``, and ``COMPLETED``/``FAILED`` are unreachable from any
``interrupt()`` result at all -- which is asserted as a negative test rather than described.

``release_terminal`` and ``fence`` are NOT interrupts and are not in this module's ladder.
A fence performs zero process and zero filesystem actions and is never upgraded to a stop.
"""
from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from . import standalone_identity as identity
from . import standalone_pty as pty_supervisor
from .standalone_lifecycle import INTERRUPT_OUTCOMES
from .standalone_profile import StandaloneProfile

#: The rungs, named.  `rung_0` is optional and profile-declared; every other rung always
#: exists, because a ladder with a conditional middle is not a ladder.
RUNGS = ("rung_0_hint", "rung_1_graceful", "rung_2_bounded_wait", "rung_3_force",
         "rung_4_proof_of_death")

#: Each gate, and what a failure at it produces.  Note that G3's failure is
#: `exit_unproven`, NOT `not_owned`: by G3 a SIGKILL has already been sent, so ownership
#: having changed does not mean nothing happened -- it means the outcome is unknown.
#:
#: The same rule governs G2 and the rung-2 wait ONCE RUNG 1 HAS DELIVERED A SIGNAL
#: (consolidated review finding 11).  `not_owned` is defined as "no signal sent, no edge
#: taken", and `lifecycle_for` maps it to NO transition -- so reporting it after a SIGTERM
#: already reached the process journalled a delivered signal as a non-event.  After any
#: delivery, a refusal is `exit_unproven`: something happened and its outcome is unknown.
GATES = {"G1": "not_owned", "G2": "not_owned", "G3": "exit_unproven"}
GATES_AFTER_SIGNAL = {"G2": "exit_unproven"}


class LadderStep(TypedDict):
    rung: str
    at: str
    identity_verified: bool
    detail: str


class InterruptResult(TypedDict):
    intent_id: str
    reason: str
    interrupt_outcome: str
    ladder: tuple[LadderStep, ...]


def _step(rung: str, *, verified: bool, detail: str = "") -> LadderStep:
    return {"rung": rung, "at": _now_iso(), "identity_verified": verified,
            "detail": detail}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _result(intent_id: str, reason: str, outcome: str,
            ladder: Sequence[LadderStep], *, signalled: bool = False) -> InterruptResult:
    if outcome not in INTERRUPT_OUTCOMES:
        raise ValueError(f"interrupt_outcome {outcome!r} is not a closed-set member")
    if outcome == "not_owned" and signalled:
        # Finding 11, enforced at the one constructor: `not_owned` may never be reported
        # once a signal has been delivered.  It means "nothing was sent"; here something was.
        outcome = "exit_unproven"
    return {"intent_id": intent_id, "reason": reason, "interrupt_outcome": outcome,
            "ladder": tuple(ladder)}


def observed_row(snapshot: Mapping[str, Any], pid: int) -> Mapping[str, Any] | None:
    """The row for ``pid``, ``{}`` when the table was READ and holds no such pid, ``None``
    only when the table could not be read.  Finding 16.

    :func:`standalone_identity.verify` already tells the two apart -- an empty mapping is
    `not_owned:pid_absent_from_table`, ``None`` is `unverifiable:process_table_unreadable`
    -- but every caller handed it `row_for(...)`, which answers ``None`` for both, so a
    process that had PROVABLY EXITED was reported as one whose liveness could not be
    established, and the proof-of-exit rung was unreachable for it.
    """
    if not snapshot.get("readable", False):
        return None
    return pty_supervisor.row_for(snapshot, pid) or {}


class Gate:
    """One re-verification gate.  Reads a FRESH snapshot; refuses a stale one.

    A gate that could be satisfied by a snapshot taken before the previous rung would
    verify nothing, so the snapshot is re-read here rather than passed in -- and
    :func:`standalone_pty.check_ownership` independently refuses one older than the
    staleness budget.
    """

    def __init__(self, record: Mapping[str, Any], profile: StandaloneProfile, *,
                 table_reader: Any = None, supervisor_pid: int | None = None,
                 drain: Any = None) -> None:
        self.record = record
        self.profile = profile
        self._table_reader = table_reader or pty_supervisor.read_process_table
        self._supervisor_pid = supervisor_pid
        # A TEARDOWN OBLIGATION, not a convenience.  An exiting session leader whose pty
        # slave holds unflushed output and whose master nobody reads wedges in the kernel's
        # "trying to exit" state, and every probe below then reports it as still present --
        # so rung 4 would return `exit_unproven` for a process that really did die.  See
        # `standalone_pty.drain`.
        self._drain = drain

    def evaluate(self) -> dict[str, Any]:
        """``{"decision": ..., "snapshot": ..., "unreadable": bool}``.

        An unreadable process table is reported as ``unreadable`` rather than raising
        through the ladder, because the ladder's response to it differs by rung: before a
        signal it is ``not_owned`` (refuse), after one it is ``exit_unproven`` (unknown).
        """
        if self._drain is not None:
            self._drain()
        snapshot = self._table_reader(self.record["captured_tty"])
        try:
            decision = pty_supervisor.check_ownership(
                self.record, snapshot,
                staleness_budget_ms=self.profile.timeouts.staleness_budget_ms,
                supervisor_pid=self._supervisor_pid)
        except pty_supervisor.ProcessTableUnreadable:
            return {"decision": None, "snapshot": snapshot, "unreadable": True}
        if decision["verdict"] == "refused" and decision["refusal"] == "stale_snapshot":
            # R-OWN-4: re-scan.  A stale snapshot is never served to a later request.
            snapshot = self._table_reader(self.record["captured_tty"])
            try:
                decision = pty_supervisor.check_ownership(
                    self.record, snapshot,
                    staleness_budget_ms=self.profile.timeouts.staleness_budget_ms,
                    supervisor_pid=self._supervisor_pid)
            except pty_supervisor.ProcessTableUnreadable:
                return {"decision": None, "snapshot": snapshot, "unreadable": True}
        return {"decision": decision, "snapshot": snapshot, "unreadable": False}


def interrupt(intent_id: str, reason: str, *, record: Mapping[str, Any],
              profile: StandaloneProfile,
              table_reader: Any = None, supervisor_pid: int | None = None,
              killpg: Any = None, kill: Any = None,
              write_hint: Any = None, sleep: Any = None,
              clock: Any = None, drain: Any = None) -> InterruptResult:
    """Run the ladder.  Every rung is gated; nothing happens before re-verification.

    ``killpg``/``kill``/``write_hint``/``sleep``/``clock`` are injectable so the whole
    ladder is deterministically testable over an injected process table -- including the
    assertions that certain rungs send NO signal, which a spy can only prove if the real
    syscall is reachable through a seam.

    ``drain`` empties the pty master before every ownership probe.  It is REQUIRED for a
    real spawn and harmless for an injected table: without it an exiting session leader can
    wedge with unflushed output and every probe reports it as still present, so rung 4
    returns ``exit_unproven`` for a process that did die.  See `standalone_pty.drain`.
    """
    now = clock or time.time
    pause = sleep or time.sleep
    gate = Gate(record, profile, table_reader=table_reader, supervisor_pid=supervisor_pid,
                drain=drain)
    ladder: list[LadderStep] = []

    # -- G0: the seven never-touch obligations -------------------------------------------
    first = gate.evaluate()
    if first["unreadable"]:
        ladder.append(_step("G1", verified=False, detail="process_table_unreadable"))
        return _result(intent_id, reason, "not_owned", ladder)
    observed = observed_row(first["snapshot"], int(record["pid"]))
    if observed == {}:
        # Finding 16.  The table was READ and this pid is not on the captured tty.  That is
        # not "unreadable" and it is not "not ours": it is the proof-of-exit question,
        # asked before any signal, and `exit_proven` answers it from the same snapshot.
        proof = pty_supervisor.exit_proven(record, first["snapshot"])
        if proof["proven"]:
            ladder.append(_step("rung_4_proof_of_death", verified=True,
                                detail=f"already exited before any signal: {proof['reason']}"))
            return _result(intent_id, reason, "interrupted_confirmed", ladder)
    try:
        permit = identity.assert_may_act(record, "signal", observed=observed)
    except identity.OwnershipRefused as exc:
        ladder.append(_step("G0", verified=False, detail=str(exc)))
        return _result(intent_id, reason, "not_owned", ladder)

    # -- G1 --------------------------------------------------------------------------------
    if first["decision"]["verdict"] != "owned":
        ladder.append(_step("G1", verified=False,
                            detail=first["decision"]["refusal"] or "not_owned"))
        return _result(intent_id, reason, "not_owned", ladder)
    ladder.append(_step("G1", verified=True))

    # -- rung 0: the optional in-band graceful hint ---------------------------------------
    # Used ONLY as rung 0.  Never as evidence of death, and never in place of the
    # OS-confirmed exit proof at rung 4.
    if profile.graceful_hint and write_hint is not None:
        try:
            write_hint(profile.graceful_hint, permit)
            ladder.append(_step("rung_0_hint", verified=True, detail="hint written"))
        except Exception as exc:  # noqa: BLE001 - a failed hint is not a failed interrupt
            ladder.append(_step("rung_0_hint", verified=True, detail=f"hint failed: {exc}"))

    # -- rung 1: graceful SIGTERM ----------------------------------------------------------
    sent = pty_supervisor.signal_target(
        record, first["decision"], pty_supervisor.graceful_signal(), permit=permit,
        snapshot=first["snapshot"], killpg=killpg, kill=kill)
    # Finding 11: from here on a refusal is reported against the fact that a signal WAS
    # delivered.  `sent` lists what actually reached a process; the withheld exit watcher
    # is listed by name and does not count.
    signalled = any(step.get("result") == "sent" for step in sent["sent"])
    withheld = sum(1 for step in sent["sent"] if str(step.get("result", "")).startswith("withheld"))
    ladder.append(_step("rung_1_graceful", verified=True,
                        detail=f"scope={sent['scope']} sent={len(sent['sent']) - withheld}"
                               + (f" withheld={withheld}" if withheld else "")))

    # -- rung 2: bounded wait, re-checking ownership AND mode AT FIRE TIME ------------------
    deadline = now() + profile.timeouts.graceful_force_timeout_ms / 1000.0
    while now() < deadline:
        pause(profile.timeouts.force_retry_ms / 1000.0)
        probe = gate.evaluate()
        if probe["unreadable"]:
            # Cannot see; do not escalate on a blind guess.  Unknown, not dead.
            ladder.append(_step("rung_2_bounded_wait", verified=False,
                                detail="process_table_unreadable"))
            return _result(intent_id, reason, "exit_unproven", ladder)
        proof = pty_supervisor.exit_proven(record, probe["snapshot"])
        if proof["proven"]:
            ladder.append(_step("rung_2_bounded_wait", verified=True,
                                detail=f"natural exit: {proof['reason']}"))
            return _result(intent_id, reason, "interrupted_confirmed", ladder)
        if probe["decision"]["verdict"] != "owned":
            # Ownership changed while we waited.  The escalation is CANCELLED, not retried:
            # a SIGKILL aimed at a recycled pid reaches a stranger.  And because rung 1
            # already delivered a signal, the outcome is UNKNOWN -- never `not_owned`.
            ladder.append(_step("rung_2_bounded_wait", verified=False,
                                detail=f"ownership changed: {probe['decision']['refusal']}"
                                       "; escalation cancelled"))
            return _result(intent_id, reason, "not_owned", ladder, signalled=signalled)
    ladder.append(_step("rung_2_bounded_wait", verified=True, detail="deadline elapsed"))

    # -- G2 --------------------------------------------------------------------------------
    second = gate.evaluate()
    if second["unreadable"] or second["decision"]["verdict"] != "owned":
        ladder.append(_step("G2", verified=False,
                            detail="process_table_unreadable" if second["unreadable"]
                            else (second["decision"]["refusal"] or "not_owned")))
        return _result(intent_id, reason, "not_owned", ladder, signalled=signalled)
    ladder.append(_step("G2", verified=True))
    observed2 = observed_row(second["snapshot"], int(record["pid"]))
    try:
        permit2 = identity.assert_may_act(record, "signal", observed=observed2)
    except identity.OwnershipRefused as exc:
        ladder.append(_step("G2", verified=False, detail=str(exc)))
        return _result(intent_id, reason, "not_owned", ladder, signalled=signalled)

    # -- rung 3: force ---------------------------------------------------------------------
    attempts = 0
    max_attempts = 2
    forced = pty_supervisor.signal_target(
        record, second["decision"], pty_supervisor.force_signal(), permit=permit2,
        snapshot=second["snapshot"], killpg=killpg, kill=kill)
    attempts += 1
    delivered = [step for step in forced["sent"] if step.get("result") == "sent"]
    ladder.append(_step("rung_3_force", verified=True,
                        detail=f"scope={forced['scope']} sent={len(delivered)} "
                               f"attempt={attempts}"))
    if not delivered and attempts < max_attempts:
        # A FAILED force reverts the mode and RE-ARMS with one fewer attempt, rather than
        # reporting a termination that was never sent.
        rearm = gate.evaluate()
        if not rearm["unreadable"] and rearm["decision"]["verdict"] == "owned":
            observed3 = observed_row(rearm["snapshot"], int(record["pid"]))
            try:
                permit3 = identity.assert_may_act(record, "signal", observed=observed3)
            except identity.OwnershipRefused:
                permit3 = None
            if permit3 is not None:
                forced = pty_supervisor.signal_target(
                    record, rearm["decision"], pty_supervisor.force_signal(),
                    permit=permit3, snapshot=rearm["snapshot"], killpg=killpg, kill=kill)
                attempts += 1
                ladder.append(_step("rung_3_force", verified=True,
                                    detail=f"re-armed attempt={attempts} "
                                           f"sent={len(forced['sent'])}"))

    # -- G3 --------------------------------------------------------------------------------
    third = gate.evaluate()
    if third["unreadable"]:
        ladder.append(_step("G3", verified=False, detail="process_table_unreadable"))
        return _result(intent_id, reason, "exit_unproven", ladder)
    ladder.append(_step("G3", verified=True))

    # -- rung 4: PROOF of death ------------------------------------------------------------
    exit_deadline = now() + profile.timeouts.physical_exit_timeout_ms / 1000.0
    while True:
        probe = gate.evaluate()
        if probe["unreadable"]:
            ladder.append(_step("rung_4_proof_of_death", verified=False,
                                detail="process_table_unreadable"))
            return _result(intent_id, reason, "exit_unproven", ladder)
        proof = pty_supervisor.exit_proven(record, probe["snapshot"])
        if proof["proven"]:
            ladder.append(_step("rung_4_proof_of_death", verified=True,
                                detail=proof["reason"]))
            return _result(intent_id, reason, "terminated_forced", ladder)
        if now() >= exit_deadline:
            break
        pause(profile.timeouts.force_retry_ms / 1000.0)
    ladder.append(_step("rung_4_proof_of_death", verified=True,
                        detail="deadline elapsed with the process still present"))
    return _result(intent_id, reason, "exit_unproven", ladder)


def lifecycle_for(outcome: str) -> dict[str, Any]:
    """The TOTAL mapping from an interrupt outcome to a lifecycle state.

    Total, and with no success branch for an unproven exit.  ``COMPLETED`` and ``FAILED``
    do not appear on the right-hand side at all: they have exactly one entry edge each and
    it is not this one.
    """
    if outcome == "interrupted_confirmed":
        return {"state": "INTERRUPTED", "lost_reason": ""}
    if outcome == "terminated_forced":
        return {"state": "INTERRUPTED", "lost_reason": ""}
    if outcome == "exit_unproven":
        return {"state": "LOST", "lost_reason": "stop_unverified"}
    if outcome in ("not_owned", "unsupported"):
        # State UNCHANGED, and no signal was sent.  A refusal is not a transition.
        return {"state": None, "lost_reason": ""}
    raise ValueError(f"interrupt_outcome {outcome!r} is not a closed-set member")


def fence(record: Mapping[str, Any]) -> dict[str, Any]:
    """Compute a fence.  ZERO process actions, ZERO filesystem actions.

    Here, in the interrupt module, precisely so the claim "a fence is never upgraded to a
    stop" is checkable in the module a stop would live in: this function calls no signal
    primitive and requests no permit, and there is no code path from its result to one.
    """
    return identity.fence_only(record)


def release_terminal(record: Mapping[str, Any], *, authority: str,
                     worker_resource: str, observed: Mapping[str, Any] | None,
                     releaser: Any = None) -> dict[str, Any]:
    """The only mutating verb outside the ladder.  Releases ONLY what was requested.

    Gated three ways: the ownership permit, ``cleanup_authority == "authorized"`` and
    ``worker_resource == "release"``.  All three, because each answers a different question
    and the executor's own gate checks the latter two -- this re-checks them so an adapter
    called directly cannot skip what the graph would have enforced.
    """
    if authority != "authorized":
        return {"released": False, "refusal": "cleanup_authority_not_authorized",
                "recovery": "retained:none"}
    if worker_resource != "release":
        return {"released": False, "refusal": "worker_resource_not_release",
                "recovery": "retained:none"}
    permit = identity.assert_may_act(record, "release_terminal", observed=observed)
    scope = identity.release_scope(record)
    if releaser is not None:
        releaser(scope, permit)
    return {"released": True, "refusal": "", "scope": scope,
            "recovery": "released:terminated"}
