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
durable ledger and ``IDEMPOTENCY_PORT_REQUIRED`` cannot fire), ``.approval_port``,
``.settlement_journal`` and -- a DIFFERENT responsibility with a DIFFERENT interface --
``.pause_row_journal``.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import standalone_identity as identity_mod
from . import standalone_interrupt as interrupt_mod
from . import standalone_journal as journal_mod
from . import standalone_pty as pty_supervisor
from . import standalone_runtime as runtime_mod
from .contracts import (BASE_CAPABILITIES, EXTERNAL_LOOKUP, EXTERNAL_RESUME,
                        LIFECYCLE_SETTLEMENT, STANDALONE_CAPABILITIES, ActionIntent,
                        ExternalLookupUnavailable, SettlementEvent)

#: The named refusal a settlement-port method reports when its authority cannot be read.
DISPATCH_UNACCOUNTED = "DISPATCH_UNACCOUNTED"


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StandaloneAdapter:
    """The standalone runtime's ``AgentExecutionPort`` (+ recovery, + lifecycle settlement)."""

    def __init__(self, runtime: Any = None, *, runtime_state: Any = None,
                 settlement_journal: Any = None, approval_port: Any = None,
                 artifact_base: str | os.PathLike[str] = ".", run_id: str = "",
                 table_reader: Any = None, pause_row_journal: Any = None) -> None:
        # `runtime=None` is a supported, deliberate wiring: `capabilities()` is a
        # declaration about the adapter TYPE and its wiring and reads no process at all, so
        # the capability authority can ask without spawning, adopting or touching anything.
        self.runtime = runtime
        self.runtime_state = runtime_state
        # TWO journals, because they answer two different questions and have two
        # different interfaces.  Conflating them is external review finding #9.
        #
        # `settlement_journal` is this runtime's own append-only `ExecutionJournal`: the
        # LifecycleSettlementPort authority behind `open_dispatches`, `axes_for`,
        # `rows_for` and `settlement_of`.  It is an NDJSON log of OBSERVATIONS.
        #
        # `pause_row_journal` is the engine's `pause_store.FileSettlementJournal`: ONE
        # promotable row per intent, written by `executor`'s PAUSE and DISPOSE nodes
        # through `row()` / `record()`.  `ExecutionJournal` has neither method, so wiring
        # it in here (which `build_standalone_adapter` did) made every pause raise
        # `AttributeError` inside `_settlement_row`'s broad `except Exception` and report
        # the misleading `DISPATCH_UNACCOUNTED`.
        #
        # There is deliberately no duck-typed shim: a `row()` bolted onto the execution
        # journal would hide exactly the distinction that got this wrong.  And
        # `graph.build_graph` is NOT taught to look for this attribute -- it is a pinned
        # policy module and the ticket's invariant is that the standalone work adds no
        # branch to it.  The COMPOSITION ROOT passes this journal to `build_graph(journal=)`
        # explicitly, which is the seam that already exists for exactly this.
        self.settlement_journal = settlement_journal
        self.pause_row_journal = pause_row_journal
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
        self._journal_planned(intent, session)
        try:
            return self._supervise(session, lease_token=lease_token)
        except runtime_mod.StandaloneDispatchUnsettled as unsettled:
            # Findings 1 and 7.  NOT a settlement, and NOT a traceback: the run stops as a
            # typed BLOCKED terminal through the engine's own idempotency vocabulary.  The
            # journal already holds the durable retained/refused state, the ledger stays
            # EFFECTED, and a later recovery finds an open, unsettled effect and refuses to
            # start work beside it.
            from .executor import IdempotencyRecoveryError
            raise IdempotencyRecoveryError(unsettled.code, str(unsettled)) from unsettled
        except Exception as exc:  # noqa: BLE001 - re-raised, AFTER lifecycle safety
            # Follow-up review finding 3.  Whatever escaped the closed failure table --
            # from `run_dispatch`, or from inside `settle_failed` itself -- is a
            # programming error and still propagates; but it propagates only once the
            # child it may have left behind is proven exited and reclaimed, or durably
            # recorded RETAINED + unsettled.  Never over a live, unowned agent.
            session.secure_after_unexpected(exc)
            raise

    def _supervise(self, session: Any, *, lease_token: str | None) -> Mapping[str, Any]:
        """Run the dispatch and settle every NAMED failure.  Unnamed ones escape to
        :meth:`start`, which secures the lifecycle before letting them out."""
        try:
            return session.run_dispatch(lease_token=lease_token)
        except runtime_mod.StandaloneDispatchFailed as failure:
            # EXTERNAL REVIEW #8.  This is the executor/launcher boundary, and before
            # this it did not exist: `executor._settle_now` calls `adapter.start` and
            # has no handler, so a readiness timeout, an auth expiry, an OOM kill or a
            # missing result propagated out of the graph as a traceback and took the
            # whole run with it.  A dispatch that could not produce a verdict is still
            # an OUTCOME, and the engine has a vocabulary for it, so it is settled here
            # as a TYPED FAILED settlement and routed by the engine's own policy.
            #
            # `settle_failed` proves the process's exit FIRST (finding 1) and raises
            # `StandaloneDispatchUnsettled` when it cannot, so this is not a catch-all
            # that can settle over a live process or swallow an incoherent state.
            return session.settle_failed(failure, lease_token=lease_token)
        except runtime_mod.StandaloneDispatchUnsettled:
            raise
        except Exception as exc:  # noqa: BLE001 - re-raised below unless NAMED
            # Consolidated review finding 6.  The runtime's OTHER production failures
            # -- an identity-binding violation, a delivery-mode mismatch, an unprovable
            # teardown, a refused ownership permit, an unreadable process table, a pty
            # refusal, a non-durable intent, an OS error -- are plain exceptions and
            # escaped exactly as `StandaloneDispatchFailed` once did.  Each is a NAMED
            # member of `failure_stage_for`'s closed table and settles under its stage;
            # anything outside the table is a programming error and still propagates --
            # through `start`, which establishes lifecycle safety first (finding 3).
            stage = runtime_mod.failure_stage_for(exc)
            if stage is None:
                raise
            failure = runtime_mod.StandaloneDispatchFailed(
                stage, f"{type(exc).__name__}: {exc}",
                session._receipt("failed", stage, teardown="not_required")
                if session.record is None else None)
            return session.settle_failed(failure, lease_token=lease_token)

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
    def _receipt_fence(self, intent_id: str) -> str:
        """The ``session_id:process_incarnation`` the DURABLE RECEIPT for this intent names.

        Empty when no ledger is wired or no receipt was recorded -- there is then nothing
        to fence against, and an empty fence never rejects, exactly as in :meth:`resume`.
        An unreadable ledger RAISES: unknown is not absence.
        """
        if self.runtime_state is None:
            return ""
        try:
            stored = self.runtime_state.get_receipt(intent_id)
        except Exception as exc:  # noqa: BLE001 - unreadable is unknown, not absent
            raise ExternalLookupUnavailable(
                f"the runtime-state ledger is unreadable: {exc}") from exc
        return str(((stored or {}).get("receipt") or {}).get("external_id") or "")

    def _journal_settlement(self, intent_id: str, *,
                            expected_fence: str) -> SettlementEvent | None:
        """This run's terminal journal row for ``intent_id``, FENCED against ``expected_fence``.

        The journal is an append-only file under the run root and a *foreign or replayed*
        incarnation can have written a terminal row into it.  A row whose
        ``session_id:process_incarnation`` contradicts the fence is therefore not this
        dispatch's settlement and is skipped -- the same rule :meth:`resume` applies to the
        receipt it is handed.  ``expected_fence=""`` disables the comparison, because a
        composition with no receipt to fence against has nothing to contradict.
        """
        rows = self.settlement_journal.rows_for(intent_id)   # raises when unreadable
        for row in reversed(rows):
            if row["kind"] != "SETTLEMENT_OBSERVED":
                continue
            event = (row.get("source_vocabulary") or {}).get("event")
            if not isinstance(event, dict):
                continue
            fence = f"{row['session_id']}:{row['process_incarnation']}"
            if expected_fence and fence != expected_fence:
                continue
            return event
        return None

    def settlement(self, intent_id: str) -> SettlementEvent | None:
        """``None`` **only** to prove absence; an unreadable authority RAISES.

        Answerable by a stranger process: it reads the ledger and the journal, both plain
        files under the run root, and holds none of the creating process's objects.

        **The journal half is FENCED.**  ``executor._recover`` asks this question FIRST, and
        harvests whatever it answers -- so with an unfenced answer here the identity fence
        in :meth:`resume` was unreachable through the production recovery ladder and a
        replayed terminal row from a foreign incarnation would have been collected as this
        dispatch's verdict.  The LEDGER's settlement is not fenced and must not be: it is
        written only by this run's executor under its own lease token, and it is the
        authority the journal file mirrors.
        """
        if self.settlement_journal is None:
            if self.runtime_state is None:
                raise journal_mod.JournalUnreadable(
                    f"{intent_id}: no settlement authority is wired; absence cannot be "
                    "proven and unknown is never None")
            return self.runtime_state.get_settlement(intent_id)
        # Read the journal FIRST, exactly as `ExecutionJournal.settlement_of` did: an
        # unreadable journal RAISES even when the ledger holds an answer, because a caller
        # must not act on a partial view of the two authorities.
        self.settlement_journal.rows_for(intent_id)          # raises when unreadable
        if self.runtime_state is not None:
            stored = self.runtime_state.get_settlement(intent_id)
            if stored is not None:
                return stored
        return self._journal_settlement(intent_id,
                                        expected_fence=self._receipt_fence(intent_id))

    def _journal_planned(self, intent: ActionIntent, session: Any) -> None:
        """Open this dispatch's PAUSE ROW, before the effect.  The ``OrcaAdapter`` pattern.

        Part of external review #9.  `pause_store.FileSettlementJournal` is the store the
        engine's PAUSE and DISPOSE nodes read and promote, and its rows carry a closed field
        set that includes ``run_id`` -- so a dispatch that never opens a row makes
        `executor._settlement_row` fail at its very first `journal.record(...)`, whatever
        journal is wired.  Fixing the WIRING without also opening the row would have moved
        the failure rather than removed it.

        Every field is this caller's own choice rather than a runtime observation, exactly
        as `OrcaAdapter._journal_planned`'s are, so none of it can be lost with the effect.
        A composition with no pause-row journal simply writes nothing here.
        """
        if self.pause_row_journal is None:
            return
        intent_id = str(intent["intent_id"])
        run_id = self.run_id or str(intent.get("run_id") or "")
        role = "phase_reviewer" if intent.get("role") != "WORKER" else "phase_worker"
        self.pause_row_journal.record(
            intent_id, stage="PLANNED", run_id=run_id,
            payload_digest=str(intent.get("payload_digest") or ""),
            task_id=str(intent.get("task_id") or ""),
            dispatch_id=str(getattr(session, "dispatch_id", "") or ""),
            terminal_title=f"os37-{run_id}-{intent_id}",
            terminal_worktree=str(getattr(session, "worktree_path", "") or ""),
            terminal_role="active_worker", terminal_origin="standalone_pty",
            terminal_intended_role=role,
            terminal_owner=str(getattr(session, "fence", "") or run_id or intent_id),
            created_by=run_id or intent_id, provenance_source="journal",
            planned_at=_now())

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
        if self.settlement_journal is None:
            return self.settlement(intent_id)
        # Consolidated review finding 2.  The row the fence VALIDATES is the row whose
        # event is RETURNED.  It used to load the latest stored settlement, separately find
        # a row whose fence matched the receipt, and then return the previously loaded
        # value -- so a matching session A followed by a foreign session B returned B's
        # event under A's receipt.  Now the fenced row's own event is the answer, and the
        # ledger -- read UNFENCED, deliberately, so this fence is falsifiable -- is
        # consulted only to confirm it settled the SAME event; a ledger that settled a
        # different one is a contradiction this method refuses rather than resolves.
        rows = self.settlement_journal.rows_for(intent_id)   # raises when unreadable
        fenced_row: Mapping[str, Any] | None = None
        for row in reversed(rows):
            if row["kind"] != "SETTLEMENT_OBSERVED":
                continue
            fence = f"{row['session_id']}:{row['process_incarnation']}"
            if expected_fence and fence != expected_fence:
                # A replayed or foreign settlement is not harvested as this one's.
                continue
            fenced_row = row
            break
        if fenced_row is None:
            # ---- follow-up review finding 1: COLLECT, do not merely look ------------
            # No settlement row for this fence.  The receipt proves an `execve` happened
            # (it is written only after the child's spawn record is read), so the effect
            # exists or existed; the crashed supervisor simply never settled it.  The
            # rebuilt runtime reconstructs the session from the durable evidence and
            # settles it exactly once -- or leaves it durably unsettled by name.
            return self._collect_in_flight(intent, expected_fence)
        matched = (fenced_row.get("source_vocabulary") or {}).get("event")
        stored = (self.runtime_state.get_settlement(intent_id)
                  if self.runtime_state is not None else None)
        if not isinstance(matched, dict):
            # The fenced row proves THIS fence settled but carries no event of its own;
            # the ledger is then the only authority for what it settled to.  Never the
            # latest journal row -- that is the row the fence just refused.
            return stored
        if stored is not None and stored.get("event_id") != matched.get("event_id"):
            return None
        return matched

    def _collect_in_flight(self, intent: ActionIntent,
                           expected_fence: str) -> SettlementEvent | None:
        """Reconstruct, fence, await and settle -- ONCE -- an in-flight dispatch.

        Nothing is spawned here, ever: `adopt` reads the child's own spawn record and the
        journal's spawn observation for the receipt's fence, and refuses when they do not
        name one and the same process.  `collect` then waits for the exit sentinel (or
        proves the exit through the identity-fenced process table / the ownership ladder)
        and takes the same single settlement edge a supervised dispatch takes.  The
        journal's admission ladder is the exactly-once guard: the fenced settlement row is
        written before the event is handed up, and a second recovery finds that row first
        (above) and collects nothing twice.  The LEDGER is written by the collecting
        executor under its own lease token, not by the adopted session.

        ``None`` only when the dispatch is still unsettled by name -- a refused adoption
        (journalled), an unownable or unreadable process -- and every such refusal leaves
        a durable row saying why.  A programming error still propagates, after lifecycle
        safety is established (finding 3).
        """
        if not expected_fence or self.runtime is None:
            return None
        runtime = self.runtime
        session, outcome = runtime.adopt_session(intent, fence=expected_fence)
        intent_id = str(intent["intent_id"])
        session_id, _, incarnation = expected_fence.partition(":")
        if session is None:
            self.settlement_journal.append(journal_mod.make_record(
                kind="REFUSED", derived_from="runtime_state", intent_id=intent_id,
                event="evidence_unreadable", state="LOST",
                lost_reason="evidence_unreadable",
                session_id=session_id, process_incarnation=incarnation,
                axes={"settlement": "not_settled", "worker_resource": "retain",
                      "process_liveness": "disputed", "cleanup_authority": "unknown"},
                source_vocabulary={"recovery": "adoption_refused",
                                   "detail": outcome.get("detail", ""),
                                   "spawn_record": outcome.get("spawn_record", "")}))
            return None
        try:
            try:
                session.collect()
            except runtime_mod.StandaloneDispatchFailed as failure:
                session.settle_failed(failure)
            except runtime_mod.StandaloneDispatchUnsettled:
                raise
            except Exception as exc:  # noqa: BLE001 - named -> settled; else secured, raised
                stage = runtime_mod.failure_stage_for(exc)
                if stage is None:
                    session.secure_after_unexpected(exc)
                    raise
                session.settle_failed(runtime_mod.StandaloneDispatchFailed(
                    stage, f"{type(exc).__name__}: {exc}"))
        except runtime_mod.StandaloneDispatchUnsettled as unsettled:
            from .executor import IdempotencyRecoveryError
            raise IdempotencyRecoveryError(unsettled.code, str(unsettled)) from unsettled
        return self._journal_settlement(intent_id, expected_fence=expected_fence)

    # ---- +5 LifecycleSettlementPort (DD-1) ----------------------------------------------
    def open_dispatches(self) -> tuple[str, ...]:
        """RAISES, never a short tuple, when the source cannot be read."""
        if self.settlement_journal is None:
            raise RuntimeError(
                f"{DISPATCH_UNACCOUNTED}: no durable settlement journal is wired")
        return self.settlement_journal.open_dispatches()

    def recover_handle(self, intent_id: str) -> Mapping[str, Any]:
        """``listing_verified`` only against a LIVE authority.  RAISES when unreadable.

        Consolidated review finding 8.  The journal NAMES the candidate -- the pty id, the
        captured tty, the pid, the argv digest -- and the journal alone can never VERIFY
        it: reading the "verified" digest and the candidate digest from the same file was a
        tautology, so the loss of the pty/process after a supervisor crash went undetected
        and a dead resource was reported `listing_verified`.

        The verifying authorities are the ones the Orca adapter's live listing stands for
        here:

        * the OS PROCESS TABLE, tty-scoped, exactly as the interrupt ladder reads it: the
          recorded pid must be present on the captured tty in the recorded process group;
        * the CHILD-WRITTEN SPAWN RECORD for the journalled incarnation: written by the
          agent itself before `execve`, it must name the same pid and the same argv digest
          the journal carries.

        Both hold -> ``listing_verified``.  A readable table without the pid ->
        ``not_listed`` (the resource is gone; nothing may be acted on).  An unreadable
        table -> ``listing_candidate`` (named for reporting, never acted on).  A present
        process whose spawn record contradicts the journal -> ``unverified``.

        A session whose exit is PROVEN is verified too, by a third live-side authority:
        the fenced exit sentinel the run's own exit watcher wrote for exactly this
        session and incarnation.  That is a resource this run can prove is its own and
        prove has ENDED -- the opposite of an orphan -- so it is ``listing_verified`` and
        the pause policy discharges it as `exited` from its own axes.
        """
        if self.settlement_journal is None:
            raise RuntimeError(
                f"{DISPATCH_UNACCOUNTED}: no durable settlement journal is wired")
        rows = self.settlement_journal.rows_for(intent_id)          # raises when unreadable
        candidate = journal_mod.recover_handle(self.settlement_journal, intent_id)
        if candidate["handle_recovery"] == "not_listed":
            return candidate
        handle = str(candidate.get("candidate") or candidate.get("handle") or "")
        tty = pid = pgid = digest = incarnation = session_id = ""
        for row in reversed(rows):
            vocab = row.get("source_vocabulary") or {}
            if not tty and vocab.get("captured_tty"):
                tty = str(vocab["captured_tty"])
            if not pid and vocab.get("pid"):
                pid = str(vocab["pid"])
            if not digest and vocab.get("session_digest"):
                digest = str(vocab["session_digest"])
            spawn = vocab.get("spawn_record")
            if isinstance(spawn, Mapping) and not pgid and spawn.get("pgid"):
                pgid = str(spawn["pgid"])
            if not incarnation and row.get("process_incarnation"):
                incarnation = str(row["process_incarnation"])
                session_id = str(row.get("session_id") or "")
        run_id = self.run_id or ""
        sentinel_path = None
        if session_id and incarnation:
            sentinel_path = pty_supervisor.exit_sentinel_path(
                self.artifact_base, run_id, session_id, incarnation)
            verified = self._verified_by_sentinel(handle, sentinel_path,
                                                  fence=f"{session_id}:{incarnation}")
            if verified is not None:
                return verified
        if not tty or not pid:
            return {"handle": None, "handle_recovery": "listing_candidate",
                    "candidate": handle,
                    "detail": "the journal names no durable process address to verify "
                              "the candidate against; it must not be acted on"}
        probe = pty_supervisor.read_spawn_records(self.artifact_base, run_id, intent_id,
                                                 incarnation=incarnation)
        record = probe.get("record") or {}
        # The group the agent must still be in: the journal's spawn observation, else the
        # child's own record, else the pid itself -- the spawn topology makes the agent
        # its own group leader (`setpgid(0, 0)`), so a pid found in ANOTHER group is a
        # recycled pid, not this dispatch's process.
        expected_pgid = pgid or str(record.get("pgid") or "") or pid
        reader = self._table_reader or pty_supervisor.read_process_table
        snapshot = reader(tty)
        if not snapshot.get("readable", False):
            return {"handle": None, "handle_recovery": "listing_candidate",
                    "candidate": handle,
                    "detail": f"the process table for {tty!r} could not be read; "
                              "unreadable is unknown, and unknown is never verified"}
        row = pty_supervisor.row_for(snapshot, int(pid))
        listed = (row is not None and row["tty"] == tty
                  and str(row["pgid"]) == expected_pgid)
        if not listed or str(row["stat"]).startswith("Z"):
            # ---- correction iteration 2 (CI-2): EXIT EVIDENCE IN FLIGHT ----------------
            # The pid is off its tty (or on it as a zombie) and no sentinel exists YET.
            # Two different facts hide behind that: the process is gone with nothing
            # to prove it (an orphan, `not_listed`), or it has just exited and the run's
            # own exit watcher -- the session leader, still alive -- is about to reap it
            # and write the fenced sentinel.  The old code answered `not_listed` for both
            # and a pause landing in the second window refused TERMINAL_ORPHAN_POSSIBLE
            # for a process that was proven ended a few milliseconds later.  While the
            # watcher lives the evidence is IN FLIGHT: wait for it, bounded, and answer
            # from the sentinel.  A watcher that is gone leaves nothing to wait for.
            leader = int(record.get("sid") or 0)
            if sentinel_path is not None:
                verified = self._await_exit_evidence(
                    handle, sentinel_path, fence=f"{session_id}:{incarnation}",
                    leader_pid=leader)
                if verified is not None:
                    return verified
            if listed and str(row["stat"]).startswith("Z"):
                return {"handle": None, "handle_recovery": "listing_candidate",
                        "candidate": handle,
                        "detail": f"pid {pid} on {tty!r} is a zombie whose exit watcher "
                                  "has not written the sentinel within the budget; "
                                  "unknown is never verified and never acted on"}
            return {"handle": None, "handle_recovery": "not_listed",
                    "candidate": handle,
                    "detail": f"pid {pid} is not on {tty!r} in the recorded process "
                              "group; the pty session this run created is gone"}
        if probe["outcome"] != "present" or str(record.get("pid")) != pid \
                or (digest and str(record.get("argv_digest") or "") != digest):
            return {"handle": None, "handle_recovery": "unverified",
                    "candidate": handle,
                    "detail": "a process holds the recorded pid and tty but the child's "
                              "own spawn record does not corroborate the journal "
                              f"({probe['outcome']}); it is not proven ours"}
        return {"handle": handle, "handle_recovery": "listing_verified"}

    #: How long a reader waits for exit evidence that is IN FLIGHT -- the watcher alive,
    #: the agent exited, the sentinel not yet written.  Measured at ~10 ms on the MVP host
    #: under load; the bound exists for a wedged watcher and is never the normal cost.
    EXIT_EVIDENCE_BUDGET_MS = 2_000

    @staticmethod
    def _verified_by_sentinel(handle: str, sentinel_path: Any, *,
                              fence: str) -> Mapping[str, Any] | None:
        sentinel = pty_supervisor.read_exit_sentinel(sentinel_path, fence=fence)
        if sentinel["outcome"] != "exited":
            return None
        return {"handle": handle, "handle_recovery": "listing_verified",
                "exit_status": sentinel["code"],
                "detail": "the run's own exit watcher wrote a fenced exit sentinel "
                          "for this session; the resource is proven ours and "
                          "proven ended"}

    def _await_exit_evidence(self, handle: str, sentinel_path: Any, *, fence: str,
                             leader_pid: int) -> Mapping[str, Any] | None:
        """The fenced sentinel, awaited while the exit watcher is ALIVE.  CI-2.

        Returns the verified answer the moment the sentinel lands; ``None`` when the
        watcher is gone (nothing will ever write it) or the budget elapses (unknown).
        The watcher is identified by the child's own spawn record (`sid` is the leader),
        so a recycled leader pid can at worst make this wait the budget, never verify.
        """
        import time
        deadline = time.time() + self.EXIT_EVIDENCE_BUDGET_MS / 1000.0
        while True:
            verified = self._verified_by_sentinel(handle, sentinel_path, fence=fence)
            if verified is not None:
                return verified
            if leader_pid <= 0 or not _pid_present(leader_pid) or time.time() >= deadline:
                return None
            time.sleep(0.005)

    #: The pause-row columns the journal can answer, and the ``source_vocabulary`` key each
    #: is read from.  Named as data so the mapping is legible and so nothing here invents a
    #: value: a column the journal never recorded stays EMPTY and the pause authority
    #: refuses the row, which is the correct outcome for a dispatch nothing named.
    _PROVENANCE_COLUMNS = {"terminal_title": "pty_id", "terminal_digest": "session_digest",
                           "terminal_role": "terminal_role",
                           "terminal_origin": "terminal_origin",
                           "terminal_owner": "terminal_owner"}

    def account_dispatch(self, intent_id: str) -> Mapping[str, Any]:
        """READ-ONLY: issues no mutation and no command, so repeating it is always safe.

        It reads the four axes and the terminal PROVENANCE off the journal.  It sends no
        signal and writes nothing -- which a syscall spy asserts rather than this docstring.

        **Why the provenance columns are here.**  They used to be absent, so every
        standalone row reached ``pause_policy.require_pause_disposition`` with
        ``provenance_source=""``, ``terminal_role=""`` and ``terminal_owner=""``; that is
        ``residual``, which is not an AC-1 discharging disposition, so a perfectly accounted
        standalone dispatch raised ``TERMINAL_OWNERSHIP_UNKNOWN`` and BLOCKED the pause.
        The journal has known these facts since the spawn -- role, origin, the identity
        fence, the pty id and its digest -- so the row now carries them and a retained
        session is discharged as ``retained_by_named_owner``, by name, with an owner a
        stranger process can re-read.  Nothing is fabricated: an intent whose journal never
        named a role still reports ``unknown_role`` and is still refused.
        """
        if self.settlement_journal is None:
            raise RuntimeError(
                f"{DISPATCH_UNACCOUNTED}: no durable settlement journal is wired")
        rows = self.settlement_journal.rows_for(intent_id)     # raises when unreadable
        axes = dict(self.settlement_journal.axes_for(intent_id))
        row: dict[str, Any] = {"intent_id": intent_id, **axes,
                               "terminal_disposition": "", "recovery": "observed",
                               "provenance_source": "journal" if rows else "absent"}
        for column in self._PROVENANCE_COLUMNS:
            row[column] = ""
        for column, key in self._PROVENANCE_COLUMNS.items():
            for record in reversed(rows):
                value = (record.get("source_vocabulary") or {}).get(key)
                if value:
                    row[column] = str(value)
                    break
        for column in ("task_id", "dispatch_id"):
            for record in reversed(rows):
                if record.get(column):
                    row[column] = str(record[column])
                    break
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
                  # `disputed`, not `unverifiable`: the pause/settlement authority owns
                  # this vocabulary and its fail-closed member for "no authority
                  # establishes this" is `disputed`.  It is never `already exited`.
                  "process_liveness": "disputed", "cleanup_authority": "unknown"},
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
        # Finding 16: `{}` for a readable table that holds no such pid, `None` only when it
        # could not be read.
        observed = interrupt_mod.observed_row(snapshot, int(session.record["pid"]))
        try:
            outcome = interrupt_mod.release_terminal(
                session.record, authority=authority,
                worker_resource=axes["worker_resource"], observed=observed,
                releaser=lambda scope, permit: session.release())
        except identity_mod.OwnershipRefused as refused:
            # A REFUSAL, returned by name -- not an exception through the PAUSE node.
            #
            # This is the same family as external review #5 and #9: `executor._settlement_row`
            # calls this verb for any row whose axes say `authorized`+`release`, which a
            # SETTLED standalone dispatch's axes do, and the ownership re-verification then
            # cannot succeed because the process has ALREADY EXITED and left the process
            # table.  The exception escaped into the pause node's broad handler and every
            # settled standalone run was reported `DISPATCH_UNACCOUNTED`.
            #
            # Nothing is released and nothing is claimed: the row keeps
            # `process_liveness="already exited"` from its own settlement record, which is
            # what discharges it as `exited`, and the refusal is named so an operator can
            # see that this process did not perform a release rather than assuming one.
            return {"recovery": "retained:ownership_unverifiable",
                    "refusal": str(refused).split(":", 2)[0] or "ownership_refused",
                    "process_liveness": axes["process_liveness"]}
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


def _pid_present(pid: int) -> bool:
    """``kill(pid, 0)``: exists (ours or not) vs ESRCH."""
    import errno
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


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
