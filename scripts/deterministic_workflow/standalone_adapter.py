"""OS-37 N12.  ``StandaloneAdapter`` -- six frozen signatures, plus two, plus five.

A **sibling** of :mod:`orca_adapter`, never a subclass, and it imports it nowhere.
``ports.AgentExecutionPort`` is ``@runtime_checkable``, so this class is a STRUCTURAL
implementation of the existing protocol: no port file is edited, no signature moves, no
default changes, no annotation changes.  Everything OS-37 adds is a new KEY inside mappings
the six methods already exchange -- which is what the execution contract means by
"additive".

Two capabilities are declared CONDITIONALLY, on the wiring that makes them honourable:

``lifecycle_settlement`` -- declared only when a durable journal is wired.  Literally the
``OrcaAdapter`` pattern.  With no journal, ``routing.pause_admissible`` refuses the route
and pause correctly falls back to BLOCK, and V-6 asserts that direction too.

``external_resume`` -- declared only with a journal AND an armed identity fence, because
each of the four conditions the contract sets is met by a NAMED mechanism: (1) the durable
pre-effect claim is ``runtime_state``'s, already taken before ``start`` is called, and this
adapter adds exactly one ``record_receipt`` write under the caller's lease token; (2) the
journal, the exit sentinel and the ledger are plain files, so a stranger process can
reconstruct the run; (3) every record carries ``session_id:process_incarnation`` and a
foreign fence is REFUSED, not merged; (4) ``lookup`` returns ``None`` only to prove absence
and RAISES when existence is unknown, and ``resume`` never synthesizes a settlement from
missing contrary evidence.  It is **withdrawn automatically** on any wiring where the
backing is absent -- and declaring it in order to skip the BLOCK would be exactly the
AC-37-20 violation the contract forbids.

The three structural obligations that appear in no signature are met by attributes the
graph reads off the adapter: ``.runtime_state`` (so ``resolve_runtime_state`` finds a
durable ledger and ``IDEMPOTENCY_PORT_REQUIRED`` cannot fire), ``.approval_port`` and
``.settlement_journal``.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import standalone_interrupt as interrupt_mod
from . import standalone_journal as journal_mod
from . import standalone_pty as pty_supervisor
from .contracts import (BASE_CAPABILITIES, EXTERNAL_LOOKUP, EXTERNAL_RESUME,
                        LIFECYCLE_SETTLEMENT, STANDALONE_CAPABILITIES, ActionIntent,
                        ExternalLookupUnavailable, SettlementEvent)

#: The named refusal a settlement-port method reports when its authority cannot be read.
DISPATCH_UNACCOUNTED = "DISPATCH_UNACCOUNTED"


class StandaloneAdapter:
    """The standalone runtime's ``AgentExecutionPort`` (+ recovery, + lifecycle settlement)."""

    def __init__(self, runtime: Any = None, *, runtime_state: Any = None,
                 settlement_journal: Any = None, approval_port: Any = None,
                 artifact_base: str | os.PathLike[str] = ".", run_id: str = "",
                 table_reader: Any = None) -> None:
        # `runtime=None` is a supported, deliberate wiring: `capabilities()` is a
        # declaration about the adapter TYPE and its wiring and reads no process at all, so
        # the capability authority can ask without spawning, adopting or touching anything.
        self.runtime = runtime
        self.runtime_state = runtime_state
        self.settlement_journal = settlement_journal
        self.approval_port = approval_port
        self.artifact_base = Path(artifact_base)
        self.run_id = run_id or getattr(runtime, "run_id", "")
        self._table_reader = table_reader
        self._events: dict[str, SettlementEvent] = {}

    # ---- 1/6 capabilities ---------------------------------------------------------------
    def capabilities(self) -> frozenset[str]:
        """What this runtime's primitives actually support -- and no others.

        The five standalone tokens are declared unconditionally because they describe what
        this runtime IS: it owns a real pty session, it proves prompt delivery rather than
        assuming it, it has an interrupt ladder, its signals are ownership-scoped, and a
        stranger process can rediscover its sessions.  The two recovery tokens and the
        lifecycle-settlement token are declared only on the wiring that makes them
        honourable, and they withdraw themselves when it is absent.
        """
        offered = BASE_CAPABILITIES | STANDALONE_CAPABILITIES | frozenset(
            {"dispatch_provenance", "runtime_ownership"})
        if self.settlement_journal is not None:
            offered = offered | frozenset({LIFECYCLE_SETTLEMENT, EXTERNAL_LOOKUP})
            if self._identity_fence_armed():
                # DD-2.  Declared because all four conditions are met by named mechanisms:
                # the reused pre-effect claim, stranger re-readability, the identity fence
                # on collection, and "unknown is not absence" in both `lookup` and `resume`.
                offered = offered | frozenset({EXTERNAL_RESUME})
        if self.approval_port is not None:
            offered = offered | frozenset({"human_approval"})
        return offered

    def _identity_fence_armed(self) -> bool:
        """Whether the fence VALUE has an authority to live in.

        The fence is only meaningful when there is a ledger to hold its value: the journal
        merely compares against ``receipt["external_id"]``, so with no ledger there is
        nothing to compare against and condition 3 is not met.
        """
        return self.runtime_state is not None

    # ---- 2/6 start ----------------------------------------------------------------------
    def start(self, intent: ActionIntent, *,
              lease_token: str | None = None) -> Mapping[str, Any]:
        """Run the WHOLE supervised dispatch, and settle before returning.  Blocking.

        **The engine's contract, not a choice.**  ``executor._settle_now`` calls this and
        then immediately requires ``settlement(intent_id)`` to answer; its own comment says
        *"``start`` is the long blocking call -- minutes, not milliseconds -- so the keeper
        renews the lease throughout it"*, which is what ``LeaseKeeper`` is for.
        ``OrcaAdapter.start`` satisfies it by running the task to completion and settling the
        ledger before returning.  An adapter that returned once the process was merely
        spawned would make every run raise ``OUT_OF_ORDER_EVENT:settlement missing`` -- so
        spawn, readiness, delivery, completion and settlement all happen here.

        ``lease_token`` is the fence the executor obtained from ``runtime_state.claim`` --
        threaded through, never defaulted away, so a caller that cannot name a live lease is
        refused by the STORE rather than writing an effect the current owner does not know
        about.  That refusal (``RuntimeStateLeaseHeld``) is the reused fence being live in
        the standalone path, and it is asserted by a test rather than assumed.

        Blocking here does NOT make the Coordinator's turn the lifecycle owner: the child is
        a ``setsid`` session leader in its own session, and the journal, exit sentinel and
        ledger are plain files, so the run is re-queryable from a stranger process whether or
        not this caller survives.  Both properties hold at once.
        """
        session = self._require_runtime().session_for(intent)
        return session.run_dispatch(lease_token=lease_token)

    def spawn_only(self, intent: ActionIntent, *,
                   lease_token: str | None = None, **kwargs: Any) -> Mapping[str, Any]:
        """Spawn and bind identity WITHOUT waiting for a settlement.

        Not the port method, deliberately -- ``start`` is, and it must satisfy the engine's
        blocking contract.  This exists for the tests and for an operator driving the
        runtime directly, so the two behaviours are separate names rather than a flag that
        could be passed by mistake at the call site the engine owns.

        **A `launch_with_prompt` driver needs the payload here too**, because for that mode
        the prompt goes on the argv and a spawn without one has no delivery to prove.  A
        caller that supplies none gets the canonical intent, which is the same payload
        ``run_dispatch`` would compose -- so "spawn only" means *do not wait for the
        settlement*, not *spawn something different*.
        """
        session = self._require_runtime().session_for(intent)
        if "payload" not in kwargs and session.profile.delivery_mode == "launch_with_prompt":
            from .standalone_runtime import _canonical
            kwargs["payload"] = _canonical(dict(intent))
        return session.start(lease_token=lease_token, **kwargs)

    # ---- 3/6 send -----------------------------------------------------------------------
    def send(self, intent_id: str, command: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._require_runtime().session(intent_id).send(command)

    # ---- 4/6 status ---------------------------------------------------------------------
    def status(self, intent_id: str) -> Mapping[str, Any]:
        return self._require_runtime().session(intent_id).status()

    # ---- 5/6 interrupt ------------------------------------------------------------------
    def interrupt(self, intent_id: str, reason: str) -> Mapping[str, Any]:
        """The ladder.  ``COMPLETED``/``FAILED`` are unreachable from any result here."""
        runtime = self.runtime
        if runtime is None:
            # No process is adopted merely to be asked.  A refusal, named.
            return {"intent_id": intent_id, "reason": reason,
                    "interrupt_outcome": "not_owned", "ladder": (),
                    "refusal": "no_runtime_bound"}
        try:
            session = runtime.session(intent_id)
        except KeyError:
            return {"intent_id": intent_id, "reason": reason,
                    "interrupt_outcome": "not_owned", "ladder": (),
                    "refusal": "no_live_session_in_this_process"}
        return session.interrupt(reason)

    # ---- 6/6 settlement -----------------------------------------------------------------
    def settlement(self, intent_id: str) -> SettlementEvent | None:
        """``None`` **only** to prove absence; an unreadable authority RAISES.

        Answerable by a stranger process: it reads the ledger and the journal, both plain
        files under the run root, and holds none of the creating process's objects.
        """
        if self.settlement_journal is None:
            if self.runtime_state is None:
                raise journal_mod.JournalUnreadable(
                    f"{intent_id}: no settlement authority is wired; absence cannot be "
                    "proven and unknown is never None")
            return self.runtime_state.get_settlement(intent_id)
        return self.settlement_journal.settlement_of(intent_id,
                                                     runtime_state=self.runtime_state)

    # ---- +2 ExternalRecoveryPort --------------------------------------------------------
    def lookup(self, intent: ActionIntent) -> Mapping[str, Any] | None:
        """Prove absence, or RAISE.  Reads the intent-scoped SPAWN RECORD.

        ``None`` only when the intent directory is readable and holds no spawn record --
        which proves no ``execve`` happened, because the child writes that record as its last
        instruction before ``execve``.  A present record means the effect MAY exist and must
        from then on be observed, never re-created.  An unreadable directory is
        ``ExternalLookupUnavailable`` -> ``IDEMPOTENCY_RECOVERY_BLOCKED``.

        **There is no claim index here.**  The claim is ``runtime_state``'s.
        """
        run_id = self.run_id or str(intent.get("run_id", ""))
        if not run_id:
            raise ExternalLookupUnavailable(
                "no run is bound; effect existence cannot be read")
        intent_id = str(intent["intent_id"])
        if self.runtime_state is not None:
            try:
                stored = self.runtime_state.get_receipt(intent_id)
            except Exception as exc:  # noqa: BLE001 - unreadable is unknown, not absent
                raise ExternalLookupUnavailable(
                    f"the runtime-state ledger is unreadable: {exc}") from exc
            if stored and (stored.get("receipt") or {}).get("external_id"):
                receipt = stored["receipt"]
                return {"intent_id": intent_id, "external_id": receipt["external_id"],
                        "task_id": receipt.get("task_id"),
                        "dispatch_id": receipt.get("dispatch_id"),
                        "source": "runtime_state_receipt"}
        probe = pty_supervisor.read_spawn_records(self.artifact_base, run_id, intent_id)
        if probe["outcome"] == "unknown":
            raise ExternalLookupUnavailable(
                f"{intent_id}: {probe['detail']}; existence is unknown, which is not absence")
        if probe["outcome"] == "absent":
            return None
        record = probe["record"] or {}
        return {"intent_id": intent_id,
                "external_id": f"{record.get('session_id','')}:"
                               f"{record.get('process_incarnation','')}",
                "source": "spawn_record"}

    def resume(self, intent: ActionIntent,
               receipt: Mapping[str, Any]) -> SettlementEvent | None:
        """Collect an OBSERVED settlement, fenced against the receipt's ``external_id``.

        Returns a ``SettlementEvent`` only from an observed settlement that passes the
        settlement predicate and whose fence matches the receipt.  Returns ``None`` --
        ``IDEMPOTENCY_RECOVERY_BLOCKED`` -- when the effect exists but has not settled, and
        RAISES when either authority is unreadable.

        **It never synthesizes.**  A missing exit sentinel plus an unverifiable process
        probe yields ``None``, not a fabricated success.  That is the named residual DR-2:
        a ``SIGKILL``ed wrapper leaves an uncollectable effect, which is fail-closed and
        correct -- the declaration promises an effect can be observed and collected WHEN IT
        SETTLED, not that every effect settles.
        """
        intent_id = str(intent["intent_id"])
        expected_fence = str((receipt or {}).get("external_id") or "")
        stored = self.settlement(intent_id)
        if stored is None:
            return None
        if self.settlement_journal is None:
            return stored
        rows = self.settlement_journal.rows_for(intent_id)   # raises when unreadable
        for row in reversed(rows):
            if row["kind"] != "SETTLEMENT_OBSERVED":
                continue
            fence = f"{row['session_id']}:{row['process_incarnation']}"
            if expected_fence and fence != expected_fence:
                # A replayed or foreign settlement is not harvested as this one's.
                continue
            return stored
        return None

    # ---- +5 LifecycleSettlementPort (DD-1) ----------------------------------------------
    def open_dispatches(self) -> tuple[str, ...]:
        """RAISES, never a short tuple, when the source cannot be read."""
        if self.settlement_journal is None:
            raise RuntimeError(
                f"{DISPATCH_UNACCOUNTED}: no durable settlement journal is wired")
        return self.settlement_journal.open_dispatches()

    def recover_handle(self, intent_id: str) -> Mapping[str, Any]:
        """``listing_verified`` only on a durable digest.  RAISES when unreadable."""
        if self.settlement_journal is None:
            raise RuntimeError(
                f"{DISPATCH_UNACCOUNTED}: no durable settlement journal is wired")
        verified = ""
        rows = self.settlement_journal.rows_for(intent_id)
        for row in reversed(rows):
            digest = (row.get("source_vocabulary") or {}).get("session_digest")
            if digest:
                verified = str(digest)
                break
        return journal_mod.recover_handle(self.settlement_journal, intent_id,
                                         verified_digest=verified)

    def account_dispatch(self, intent_id: str) -> Mapping[str, Any]:
        """READ-ONLY: issues no mutation and no command, so repeating it is always safe.

        It reads the four axes off the journal and, where a live session exists in this
        process, refines the liveness axis from a process probe.  It sends no signal and
        writes nothing -- which a syscall spy asserts rather than this docstring.
        """
        if self.settlement_journal is None:
            raise RuntimeError(
                f"{DISPATCH_UNACCOUNTED}: no durable settlement journal is wired")
        axes = dict(self.settlement_journal.axes_for(intent_id))
        row: dict[str, Any] = {"intent_id": intent_id, **axes,
                               "terminal_disposition": "", "recovery": "observed"}
        return row

    def recover_dispatch(self, intent_id: str, *, reason: str) -> Mapping[str, Any]:
        """Marks the row ``recovered``.  **Never ``settled``.**

        The distinction is the point: a recovered dispatch has been accounted for, and a
        settled one has produced a verdict.  Reporting the first as the second is how an
        unfinished dispatch is discharged.
        """
        if self.settlement_journal is None:
            raise RuntimeError(
                f"{DISPATCH_UNACCOUNTED}: no durable settlement journal is wired")
        self.settlement_journal.append(journal_mod.make_record(
            kind="EVENT", derived_from="runtime_state", intent_id=intent_id,
            event="settlement_accepted", state="LOST", lost_reason="settlement_unconfirmed",
            axes={"settlement": "recovered", "worker_resource": "retain",
                  "process_liveness": "unverifiable", "cleanup_authority": "unknown"},
            source_vocabulary={"reason": reason}))
        return {"settlement": "recovered",
                "recovery": f"abandon:outcome_unknown:{reason}"}

    def release_terminal(self, intent_id: str, *, authority: str) -> Mapping[str, Any]:
        """The ONLY mutating verb.  Gated on the permit AND both axis values.

        Releases only what was requested -- never a worktree, never a pre-existing or
        user-taken-over resource -- because :func:`standalone_identity.release_scope` names
        the scope as data and this method cannot widen it.
        """
        if self.settlement_journal is None:
            raise RuntimeError(
                f"{DISPATCH_UNACCOUNTED}: no durable settlement journal is wired")
        axes = self.settlement_journal.axes_for(intent_id)
        session = None
        if self.runtime is not None:
            try:
                session = self.runtime.session(intent_id)
            except KeyError:
                session = None
        if session is None or session.record is None:
            # Nothing this process owns.  Retained, and said so -- not a silent success.
            return {"recovery": "retained:none",
                    "refusal": "no_live_session_in_this_process",
                    "process_liveness": axes["process_liveness"]}
        snapshot = session._snapshot()
        observed = pty_supervisor.row_for(snapshot, int(session.record["pid"])) \
            if snapshot.get("readable") else None
        outcome = interrupt_mod.release_terminal(
            session.record, authority=authority,
            worker_resource=axes["worker_resource"], observed=observed,
            releaser=lambda scope, permit: session.release())
        if outcome["released"]:
            self.settlement_journal.append(journal_mod.make_record(
                kind="RELEASED", derived_from="pty", intent_id=intent_id,
                event="exit_observed", state=session.state or "INTERRUPTED",
                session_id=session.session_id,
                process_incarnation=session.incarnation,
                axes={"settlement": "recovered", "worker_resource": "release",
                      "process_liveness": "already exited",
                      "cleanup_authority": "authorized"},
                source_vocabulary={"scope": outcome["scope"]}))
        return {"recovery": outcome["recovery"], "refusal": outcome["refusal"]}

    # ---- helpers -------------------------------------------------------------------------
    def _require_runtime(self) -> Any:
        if self.runtime is None:
            raise RuntimeError(
                "this StandaloneAdapter is bound to no runtime; it was built to answer "
                "capabilities() and the durable settlement questions without adopting a "
                "process, and adopting one here would make the run look alive to the gate "
                "deciding whether it is stalled")
        return self.runtime


def _observation_base() -> Any:
    """``recovery_runtime.RunObservationAdapter``, imported LAZILY.

    Lazily because :mod:`recovery_runtime` pulls in the pause and watchdog machinery, and
    this module must import and pass in the LangGraph-absent lane: the standalone runtime is
    stdlib-only, and only the *recovery* path needs LangGraph -- which already refuses by
    name before any claim when it is missing.  Keeping the import inside a function also
    keeps :mod:`standalone_adapter` importable in a bare interpreter that only wants
    ``capabilities()``.
    """
    from . import recovery_runtime
    return recovery_runtime.RunObservationAdapter


class _StandaloneOrcaState:
    """``orca_state`` over the STANDALONE runtime's own durable dispatch state -- DD-3.

    Only ``orca_state`` is overridden.  Every other method of the base class is already
    filesystem-only and runtime-neutral, which is why **no edit to ``recovery_runtime.py``
    is required**.

    The base class is passed ``runner=None`` DELIBERATELY: with no runner its ``orca_state``
    raises ``ObservationUnsupported``, and that is exactly the method this mixin replaces.
    No Orca CLI is ever invoked.

    The three-way discipline is kept exactly:

    * an authority that exists but cannot be read -> ``ObservationUnavailable``
    * an authority that exists and answers "none"  -> an empty tuple, i.e. an ABSENCE
    * a fact nothing covers at all                 -> ``ObservationUnsupported``

    **F6 / F7 are not relaxed to "best-effort".**  ``FACT_CONTRIBUTORS`` is not edited; this
    adapter simply becomes a REAL authority for the same fact, so a standalone run stops
    classifying ``UNSUPPORTED_FAIL_CLOSED``.  Relaxing the rule instead would make a
    safety-relevant veto guessable in the success direction.
    """

    def __init__(self, artifact_base: str | os.PathLike[str] = ".", *,
                 journal_factory: Any, capabilities: Any = None, clock: Any = None,
                 owner_id: str | None = None) -> None:
        super().__init__(artifact_base, runner=None, capabilities=capabilities,
                         clock=clock, owner_id=owner_id)
        self._journal_factory = journal_factory

    def orca_state(self, run_id: str) -> Mapping[str, Any]:
        from .watchdog_observation import ObservationUnavailable, ObservationUnsupported
        if self._journal_factory is None:
            raise ObservationUnsupported(
                f"{run_id}: no standalone dispatch authority is wired")
        try:
            journal = self._journal_factory(run_id)
        except Exception as exc:  # noqa: BLE001 - a factory failure leaves this uncovered
            raise ObservationUnsupported(
                f"{run_id}: no standalone dispatch authority: {exc}") from exc
        if journal is None:
            raise ObservationUnsupported(
                f"{run_id}: no standalone dispatch authority is wired")
        try:
            open_rows = journal.open_dispatches()
        except journal_mod.JournalUnreadable as exc:
            # The authority EXISTS and cannot be read.  That is a refusal (F1), never
            # "no dispatch is running".
            raise ObservationUnavailable(f"{run_id}: {exc}") from exc
        except OSError as exc:
            raise ObservationUnavailable(f"{run_id}: {exc}") from exc
        # An empty tuple is an ABSENCE, not an UNSUPPORTED.  That is the whole point of
        # wiring this: F6/F7 gain a real second authority.
        return {"active_dispatches": tuple(open_rows),
                "runnable_actions": tuple(open_rows)}


_OBSERVATION_CLASS: Any = None


def observation_class() -> Any:
    """The real ``RunObservationPort`` subclass, built and cached on first use.

    Built here rather than declared at module scope so the base-class import stays lazy.
    It is a genuine subclass, so ``isinstance(obs, RunObservationAdapter)`` holds and every
    non-overridden method is the base's own.
    """
    global _OBSERVATION_CLASS
    if _OBSERVATION_CLASS is None:
        _OBSERVATION_CLASS = type("StandaloneRunObservation",
                                  (_StandaloneOrcaState, _observation_base()),
                                  {"__doc__": _StandaloneOrcaState.__doc__,
                                   "__module__": __name__})
    return _OBSERVATION_CLASS


def StandaloneRunObservation(artifact_base: str | os.PathLike[str] = ".", *,
                             journal_factory: Any, capabilities: Any = None,
                             clock: Any = None, owner_id: str | None = None) -> Any:
    """Construct the standalone ``RunObservationPort`` -- DD-3.

    Spelled as a callable rather than a bare class so the base import stays lazy; it
    constructs the real subclass :func:`observation_class` returns, so callers that check
    ``isinstance`` against ``recovery_runtime.RunObservationAdapter`` still succeed.
    """
    return observation_class()(artifact_base, journal_factory=journal_factory,
                               capabilities=capabilities, clock=clock, owner_id=owner_id)


#: Kept as an alias because DESIGN D10.2's wiring snippet names a factory.
make_run_observation = StandaloneRunObservation
