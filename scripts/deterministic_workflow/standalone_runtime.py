"""OS-37 N11.  The composition root BELOW the adapter.

Without this module :mod:`standalone_adapter` would have to import ten modules and hold the
process state itself, and the conformance body could not construct a standalone adapter
over an INJECTED process table.  This is the seam every deterministic test injects at, and
that is the whole reason it exists.

One :class:`StandaloneSession` per intent.  The session owns the pty, the capture, the
ownership record and the driver; it owns no claim, no lease and no lock -- those are the
engine's, reused unchanged.  Its ``start`` writes exactly ONE thing to the runtime-state
ledger: ``record_receipt`` under the lease token the frozen ``start`` signature already
carries, flipping ``CLAIMED -> EFFECTED`` as soon as the child's spawn record is read.  It
writes no claim of its own, because ``executor``'s ``runtime_state.claim(intent)`` already
took the durable pre-effect claim before ``adapter.start`` was called.

The Coordinator's conversational turn owns NOTHING here.  The child is a ``setsid`` session
leader, so it is not in the Coordinator's process group and does not die with it; the
journal, the exit sentinel and the ledger are plain files under the run root, so a stranger
process can re-query all of it.
"""
from __future__ import annotations

import os
import select
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict

from . import standalone_capture as capture_mod
from . import standalone_drivers as drivers
from . import standalone_env as env_policy
from . import standalone_identity as identity
from . import standalone_interrupt as interrupt_mod
from . import standalone_journal as journal_mod
from . import standalone_lifecycle as lifecycle
from . import standalone_preflight as preflight_mod
from . import standalone_pty as pty_supervisor
from .standalone_profile import StandaloneProfile

#: What `start()` returns.  `start_unknown` IS A FAILURE: the spawn was attempted and its
#: result cannot be established, and it routes to LOST at the lifecycle layer.
START_OUTCOMES = lifecycle.START_OUTCOMES


#: The trivial no-op payload both preflight rehearsals hand over.  It is deliberately the
#: smallest thing that can produce a real turn: the rehearsals exist to establish that the
#: declared selectors FIRE, not to do work, and a large payload would spend an operator's
#: quota on a check.
REHEARSAL_PAYLOAD = "Reply with exactly: OK"


class StandaloneDispatchFailed(RuntimeError):
    """A supervised dispatch could not reach a settlement, and says WHY by name.

    Raised rather than returning, because the engine's ``_settle_now`` requires a settlement
    immediately after ``start`` and its fallback is the generic
    ``OUT_OF_ORDER_EVENT:settlement missing`` -- which tells an operator nothing.  A named
    stage plus a named reason is the difference between "the readiness deadline expired" and
    "something went wrong somewhere".
    """

    def __init__(self, stage: str, reason: str,
                 receipt: Mapping[str, Any] | None = None,
                 exit_status: int | None = None) -> None:
        super().__init__(f"STANDALONE_DISPATCH_FAILED:{stage}"
                         + (f" ({reason})" if reason else ""))
        self.stage = stage
        self.reason = reason
        self.receipt = dict(receipt or {})
        # ---- OS-37 correction R5 ------------------------------------------------------
        # The waitpid-sourced exit status, when the failing leg observed one.  Without it a
        # dispatch that DIED non-zero and one that exited CLEANLY without ever writing a
        # completion record settle to byte-identical journal evidence -- both `LOST` with
        # `exit_code_unmapped` -- so an operator cannot tell a crash from a silent CLI, and
        # neither can a regression test claiming to lock two different causes.  `None` means
        # "no exit status was observed", never "exited 0": the two are different facts and
        # `map_exit_code` already refuses to conflate them.
        self.exit_status = exit_status


class StandaloneDispatchUnsettled(RuntimeError):
    """A dispatch that MUST NOT settle, and says why by name.  Findings 1 and 7.

    Two situations, and both are fail-closed by construction rather than by policy:

    * ``teardown_unproven`` -- the dispatch did not complete, the interrupt ladder ran, and
      the process's exit could NOT be proven.  A settlement here would be
      `settled/release` over a process that may still be running in the worktree, and
      the next dispatch would start beside it.  So NOTHING is settled: the journal
      records a durable RETAINED state (`not_settled` / `retain` / `disputed`), the
      ledger stays `EFFECTED`, and this is raised so the engine stops the run as a typed
      BLOCKED terminal.  A successor's recovery ladder then finds an open, unsettled
      effect and refuses to re-run it.
    * ``settlement_refused`` -- the journal's admission ladder REFUSED the settlement
      record (`SETTLEMENT_CONFLICT`, `SETTLEMENT_IDENTITY_MISMATCH`,
      `FOREIGN_INCARNATION`).  The ledger is then NOT written, because a ledger that says
      settled beside a journal that holds no terminal row is the permanent inconsistency
      finding 7 names.

    ``code`` is a member of the engine's existing idempotency vocabulary so the executor
    boundary can project it onto a terminal without learning a standalone word.
    """

    def __init__(self, cause: str, detail: str, *, code: str = "IDEMPOTENCY_RECOVERY_BLOCKED",
                 evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(f"STANDALONE_DISPATCH_UNSETTLED:{cause}: {detail}")
        self.cause = cause
        self.detail = detail
        self.code = code
        self.evidence = dict(evidence or {})


def failure_stage_for(exc: BaseException) -> str | None:
    """The NAMED stage a runtime exception settles under, or ``None`` when it is not one
    of the runtime's own failure types.  Consolidated review finding 6.

    Every member here is an exception this runtime RAISES on a production path --
    identity binding, delivery mode, teardown proof, ownership, the process table, the pty,
    the journal -- and each one used to escape `adapter.start` as a plain exception,
    through `executor._settle_now`, killing the graph with a traceback instead of settling
    a typed outcome.  The table is CLOSED: a `TypeError` or a `KeyError` is a programming
    error, not a dispatch outcome, and it still propagates.
    """
    table: tuple[tuple[type[BaseException], str], ...] = (
        (drivers.IdentityBindingUnverified, "identity_binding_violated"),
        (drivers.DeliveryModeMismatch, "delivery_mode_mismatch"),
        (identity.StandaloneTeardownUnproven, "teardown_unproven"),
        (identity.OwnershipRefused, "ownership_refused"),
        (pty_supervisor.ProcessTableUnreadable, "process_table_unreadable"),
        (pty_supervisor.PtyRefused, "pty_refused"),
        (journal_mod.ExecutionJournal.IntentNotDurable, "delivery_intent_not_durable"),
        (OSError, "os_error"),
    )
    for kind, stage in table:
        if isinstance(exc, kind):
            return stage
    return None


class _CapturedBody:
    """The agent's captured transcript, in the shape the SHARED result parser reads.

    A shim rather than a second parser: ``decision_contract.parse_agent_settlement`` reads
    ``attempt.body``, and giving it the transcript means the standalone path derives its
    settlement result through byte-identical policy to the Orca and fake paths.  A
    standalone-specific parser would be the per-runtime divergence AC-37-20 forbids.
    """

    def __init__(self, body: str) -> None:
        self.body = body


def _default_result_parser(attempt: Any, intent: Mapping[str, Any]) -> dict[str, Any]:
    """``decision_contract.parse_agent_settlement``, imported lazily.

    Lazy and here for the same reason ``orca_adapter`` does it: this module is inside the
    shipped engine package and ``decision_contract`` is a ``tools/`` sibling, so a
    module-scope import would make the package unimportable whenever the sibling is absent.
    """
    try:                                              # repository layout
        from scripts import decision_contract
    except ImportError:                               # pragma: no cover - flat installed
        import decision_contract                      # type: ignore[no-redef]
    return decision_contract.parse_agent_settlement(attempt, intent)


class StartReceipt(TypedDict):
    intent_id: str
    session_id: str
    process_incarnation: str
    host_scope: str | None
    pty_id: str
    captured_tty: str
    spawn_token: str          # diagnostic ONLY, never ownership proof
    start_outcome: str
    failure_reason: str       # present iff start_outcome != "ready"
    teardown: str             # "proven" | "not_required"


class StandaloneSession:
    """One agent process, owned by this runtime, for one intent."""

    def __init__(self, *, intent: Mapping[str, Any], profile: StandaloneProfile,
                 artifact_base: str | os.PathLike[str], run_id: str,
                 journal: journal_mod.ExecutionJournal,
                 runtime_state: Any = None,
                 repo_id: str = "standalone", worktree_path: str | None = None,
                 agent_id: str = "standalone-agent",
                 table_reader: Any = None, spawner: Any = None,
                 secret_resolver: Any = None, clock: Any = None,
                 supervisor_pid: int | None = None,
                 measured_ingest_rate: float | None = None) -> None:
        self.intent = dict(intent)
        self.profile = profile
        self.artifact_base = Path(artifact_base)
        self.run_id = run_id
        self.journal = journal
        self.runtime_state = runtime_state
        self.driver = drivers.driver_for(profile)
        #: The dispatch ROLE, carried so the journal can name who this pty session belongs
        #: to.  `pause_policy.terminal_disposition` discharges a retained resource only when
        #: its row names a role, an origin AND an owner; without those three a live,
        #: perfectly accounted standalone dispatch is `residual` and BLOCKS the pause.
        self.role = str(intent.get("role") or "")
        self.repo_id = repo_id
        self.worktree_path = worktree_path or os.getcwd()
        self.agent_id = agent_id
        self._table_reader = table_reader or pty_supervisor.read_process_table
        self._spawner = spawner or pty_supervisor.spawn
        self._secret_resolver = secret_resolver
        self._clock = clock or time.time
        self._supervisor_pid = supervisor_pid
        self._measured_ingest_rate = measured_ingest_rate

        # Minted BEFORE the spawn.  That ordering is what makes R-B's equality check
        # meaningful: the value cannot have come out of the child's own output.
        # The incarnation is minted FIRST, because `dispatch_id` is derived from it.
        self.incarnation = identity.mint_incarnation()
        self.session_id = identity.mint_session_id(
            run_id=run_id, dispatch_id=self.dispatch_id, task_id=self.task_id)
        self.spawn_token = identity.mint_spawn_token()

        self.record: dict[str, Any] | None = None
        self.pty: dict[str, Any] | None = None
        self.capture = capture_mod.BoundedCapture(
            capture_mod.capture_path(artifact_base, run_id, self.session_id),
            limits=profile.capture)
        self.event_log: list[str] = []
        self.state = "STARTING"
        self.lost_reason = ""
        self._child_env: dict[str, str] = {}
        #: The ADOPTED identity (D4.4 A-1..A-6), frozen on first acceptance and compared by
        #: EQUALITY forever after.  Empty means "not yet observed", never "any id will do".
        self.adopted_id = ""
        #: The `DELIVERY_INTENT` this dispatch journalled before its fork, or ``None``.
        self.delivery_intent: dict[str, Any] | None = None
        #: The `DeliveryProof` this dispatch constructed, or ``None``.  ``None`` never
        #: settles anything: D4.3c's precedence rule lets a typed terminal outcome settle a
        #: run whether or not a proof was ever constructed.
        self.delivery_proof: dict[str, Any] | None = None

    # -- identity ------------------------------------------------------------------------
    @property
    def fence(self) -> str:
        return f"{self.session_id}:{self.incarnation}"

    @property
    def intent_id(self) -> str:
        return str(self.intent.get("intent_id", ""))

    @property
    def task_id(self) -> str:
        """The standalone runtime's TASK identity: the intent's own stable id.

        The canonical ``ActionIntent`` carries no ``task_id`` -- that is an Orca Task, which
        ``OrcaAdapter`` obtains from ``create_task``.  A standalone runtime has no external
        system to ask, and the honest answer is not an empty string: the stable unit of work
        IS the intent, so its id is the task id.  Derived rather than defaulted, so the
        six-axis binding AC-37-01 requires stays real instead of carrying a blank.

        An explicit ``task_id`` on the intent still wins, because a caller that has one is
        naming something this runtime should not overwrite.
        """
        return str(self.intent.get("task_id") or f"task:{self.intent_id}")

    @property
    def dispatch_id(self) -> str:
        """The standalone runtime's DISPATCH identity: this intent, this incarnation.

        A dispatch is one attempt at the task, so the incarnation is exactly what
        distinguishes them -- a retry after a failed spawn is a new dispatch of the same
        task, and the fence already says so.  Deriving it from the incarnation means the
        journal's ``dispatch_id`` and its identity fence can never disagree about which
        attempt a record belongs to, which is what S-3's stale-dispatch refusal compares.
        """
        return str(self.intent.get("dispatch_id")
                   or f"dispatch:{self.intent_id}:{self.incarnation}")

    # -- D4.4 A-1..A-6: the `adopted` identity binding -----------------------------------
    def _binding_identity(self, text: str) -> str:
        """The identity R-B compares by EQUALITY, for EITHER binding mode.

        `minted_echo` returns the value this runtime minted before the spawn -- unchanged,
        and still the only thing R-B accepts for that mode.

        `adopted` is not weaker, and the difference is worth stating because it looks it.
        The CLI mints its own id and exposes no caller-supplied channel (D4.0 M-7, and M-10
        measured that supplying one is SILENTLY ignored), so the binding rests on:

          A-1  channel provenance the OS establishes -- the record arrived on the master fd
               THIS runtime created, whose slave was handed only to this child after
               `os.closerange(3, MAXFD)`.  No other process holds a writable end, and R-A
               independently confirms the pty's foreground image is `realpath(profile.binary)`;
          A-2  the FIRST record of a declared type, and only the first -- a second declared
               record cannot re-bind;
          A-3  an irrevocable freeze into the runtime-state receipt's `external_id`, under
               the lease token the frozen `start` signature already carries.  The freeze uses
               the EXISTING authority and adds none, and the journal holds no authority over
               it;
          A-4  equality forever after.  A second, DIFFERENT id is
               `identity_binding_violated` and the run settles from NEITHER.

        Returns ``""`` while no declared record has yet arrived, which is exactly R-B
        unsatisfied -- never "accept anything".
        """
        if self.profile.identity_binding != "adopted":
            return self.session_id
        observed = self.driver.bound_readiness_signal(text, minted_session_id="",
                                                      adopt=True)
        if observed is None:
            return self.adopted_id
        offered = str(observed.get("session_id") or "")
        if not offered:
            return self.adopted_id
        if not self.adopted_id:
            self.adopted_id = offered
            self._journal(kind="EVENT", derived_from="pty", event="identity_bound",
                          state=self.state,
                          vocabulary={"identity_binding": "adopted",
                                      "adopted_external_id": offered,
                                      "record_type": observed.get("record_type"),
                                      "note": "frozen into the runtime-state receipt; the "
                                              "journal records the observation and holds "
                                              "no authority over it"})
        elif offered != self.adopted_id:
            # A-4.  Not a warning and not a re-bind: the fence refuses settlements carrying
            # EITHER id, so the run cannot be settled from a stream two identities wrote to.
            self.state = "FAILED"
            self._journal(kind="REFUSED", derived_from="pty", event="evidence_unreadable",
                          state="FAILED",
                          vocabulary={"failure_reason": "identity_binding_violated",
                                      "frozen": self.adopted_id, "second": offered})
            raise drivers.IdentityBindingUnverified(
                f"a second, different identity {offered!r} arrived after {self.adopted_id!r} "
                "was frozen; this run is settled from neither")
        return self.adopted_id

    def _snapshot(self) -> Mapping[str, Any]:
        tty = (self.record or {}).get("captured_tty", "")
        return self._table_reader(tty) if tty else {
            "tty": "", "captured_at": self._clock(), "rows": (), "readable": False}

    def _axes(self, *, settlement: str, worker_resource: str, process_liveness: str,
              cleanup_authority: str) -> dict[str, str]:
        return {"settlement": settlement, "worker_resource": worker_resource,
                "process_liveness": process_liveness,
                "cleanup_authority": cleanup_authority}

    def _terminal_provenance(self) -> dict[str, str]:
        """Who owns the pty session this dispatch created, in the PAUSE authority's columns.

        `pause_policy` discharges a retained resource as ``retained_by_named_owner`` only
        when the row carries a provenance source, a role, an origin and an owner.  The
        standalone runtime can answer all four honestly and durably -- the role comes from
        the intent, the origin is this runtime, and the owner is the identity fence a
        stranger process re-reads off the journal -- so it writes them at spawn, when they
        are facts, rather than leaving a live and fully accounted dispatch to be classified
        ``residual`` and BLOCK the pause.

        There is deliberately no fabrication: an intent that named no role yields
        ``unknown_role``, which `pause_policy` refuses exactly as it should.
        """
        return {"terminal_role": self.role or "unknown_role",
                "terminal_origin": "standalone_pty",
                "terminal_owner": self.fence,
                "agent_id": self.agent_id}

    def _journal(self, *, kind: str, derived_from: str, event: str = "",
                 state: str = "", lost_reason: str = "",
                 axes: Mapping[str, str] | None = None,
                 vocabulary: Mapping[str, Any] | None = None, **extra: Any) -> None:
        self.journal.append(journal_mod.make_record(
            kind=kind, derived_from=derived_from, event=event,
            state=state or self.state, lost_reason=lost_reason,
            intent_id=self.intent_id,
            dispatch_id=self.dispatch_id, task_id=self.task_id,
            session_id=self.session_id, process_incarnation=self.incarnation,
            axes=dict(axes or self._axes(settlement="not_settled",
                                         worker_resource="retain",
                                         process_liveness="disputed",
                                         cleanup_authority="not_authorized")),
            source_vocabulary=dict(vocabulary or {}), **extra))

    # -- start ---------------------------------------------------------------------------
    def rehearse_delivery_mode(self, profile: StandaloneProfile,
                               child_env: Mapping[str, str]) -> dict[str, Any]:
        """D4.2b Check 1, for real: spawn the profile's own argv and OBSERVE.

        It observes; :func:`standalone_preflight.check_delivery_mode` decides.  Keeping the
        two apart is what lets the deterministic suite drive every branch from a recorded
        stream and the live gate drive the same branches from a real binary, without either
        one owning the decision rule.

        **Both directions are probed**, and that is because of M-10's SHAPE of failure: a
        CLI that IGNORES an input rather than rejecting it produces a SILENT mismatch, so a
        rehearsal that looked only for the declared behaviour would pass a profile that is
        wrong.  So a `launch_with_prompt` declaration is additionally spawned ONCE MORE with
        no prompt at all, and a spawn that reaches the quorum and then waits makes the
        declaration `delivery_mode_ambiguous`.
        """
        driver = drivers.driver_for(profile)
        rehearsal_session = identity.mint_session_id(
            run_id=self.run_id, dispatch_id=self.dispatch_id, task_id=self.task_id)
        payload = REHEARSAL_PAYLOAD
        intent = drivers.make_delivery_intent(
            intent_id="rehearsal", dispatch_id="rehearsal", task_id="rehearsal",
            session_id=rehearsal_session, payload=payload, argv_digest="rehearsal",
            attempt_incarnation="rehearsal", delivery_mode=profile.delivery_mode)
        observation: dict[str, Any] = {
            "r_b_closed": False, "delivery_proof": False, "auth_marker": None,
            "waited_without_prompt": False, "evaluable": False, "identity_bound": False,
            "detail": {}}
        if profile.delivery_mode == "launch_with_prompt":
            argv = list(driver.launch_argv(session_id=rehearsal_session, prompt=payload))
        else:
            argv = list(driver.argv(session_id=rehearsal_session))
        probe = preflight_mod.probe_on_pty(
            argv, child_env, timeout_ms=profile.timeouts.preflight_timeout_ms,
            cwd=self.worktree_path)
        if probe["outcome"] == "unreadable":
            observation["detail"] = {"probe": probe["outcome"]}
            return observation
        observation["evaluable"] = True
        text = probe["output"]
        adopt = profile.identity_binding == "adopted"
        bound = driver.bound_readiness_signal(
            text, minted_session_id="" if adopt else rehearsal_session, adopt=adopt)
        observation["r_b_closed"] = bound is not None
        observation["identity_bound"] = bound is not None
        if bound is not None and adopt:
            intent = dict(intent)
            intent["session_id"] = str(bound.get("session_id") or "")
        proof = driver.delivery_evidence(text, intent=intent, composed_argv=argv)
        observation["delivery_proof"] = proof is not None
        # W-2: the POSITIVE scan for a typed auth/setup marker.  It runs whether or not the
        # delivery leg passed, so a KNOWN reason is never reported as `unverified`.
        observation["auth_marker"] = driver.auth_marker_present(text)
        observation["detail"] = {
            "probe": probe["outcome"], "exit_code": probe.get("exit_code"),
            "record_types": [r.get("type") for r in driver.structured_records(text)][:20],
            "bytes": len(text)}
        # The SECOND, opposite-direction spawn: no prompt at all.
        if profile.delivery_mode == "launch_with_prompt":
            no_prompt = preflight_mod.probe_on_pty(
                list(driver.argv(session_id=rehearsal_session)), child_env,
                timeout_ms=profile.timeouts.preflight_timeout_ms,
                cwd=self.worktree_path)
            waiting_bound = driver.bound_readiness_signal(
                no_prompt["output"], minted_session_id="" if adopt else rehearsal_session,
                adopt=adopt)
            observation["waited_without_prompt"] = bool(
                no_prompt["outcome"] == "timeout" and waiting_bound is not None)
            observation["detail"]["no_prompt_probe"] = no_prompt["outcome"]
        else:
            observation["waited_without_prompt"] = bool(
                probe["outcome"] == "timeout" and bound is not None)
        return observation

    def rehearse_readiness(self, profile: StandaloneProfile,
                           child_env: Mapping[str, str],
                           minted_session_id: str) -> dict[str, Any] | None:
        """The REAL readiness rehearsal: a bounded, non-interactive spawn that proves R-B.

        DESIGN D6.1 makes this a preflight CHECK rather than a runtime fallback, and it is
        the load-bearing half of F-002's fix: R-B is the only accepting evidence ``READY``
        has, so a profile whose declared selector never fires must refuse the run BEFORE any
        spawn -- with the named reason ``profile_readiness_unverified`` -- instead of
        silently degrading to reading a terminal title.

        It was a caller-supplied hook, and that was wrong: the launcher composes the adapter
        and supplies no hook, so ``--adapter standalone`` refused every run at preflight.  A
        refusal nobody can satisfy is not fail-closed, it is unusable.

        The rehearsal is the real thing: the driver's own argv, under the same constructed
        child env, on a pty this runtime creates, with a session id minted for the rehearsal
        alone.  It returns the bound signal it observed, or ``None`` -- and ``None`` is what
        preflight turns into the named refusal.
        """
        driver = drivers.driver_for(profile)
        # The profile's OWN argv composition, in the mode it declares.  A `launch_with_prompt`
        # driver composed WITHOUT a payload emits nothing at all (D4.0 M-5, M-6, M-9), so a
        # rehearsal that omitted it would refuse every such profile at
        # `profile_readiness_unverified` -- a refusal nobody can satisfy, which is not
        # fail-closed but unusable.  The payload is a trivial no-op, exactly as D4.2b
        # specifies.
        if profile.delivery_mode == "launch_with_prompt":
            argv = driver.launch_argv(session_id=minted_session_id,
                                      prompt=REHEARSAL_PAYLOAD)
        else:
            argv = driver.argv(session_id=minted_session_id)
        probe = preflight_mod.probe_on_pty(
            list(argv), child_env,
            timeout_ms=profile.timeouts.preflight_timeout_ms,
            cwd=self.worktree_path)
        if probe["outcome"] not in ("completed", "timeout"):
            # `interactive` or `unreadable`: the rehearsal did not establish the selector,
            # and preflight must not read that as established.
            return None
        # `adopted` (D4.4 A-1..A-6): the CLI mints its own identity, so there is nothing to
        # compare a rehearsal's record against -- the freeze happens at RUN time, against
        # the run's own first declared record.  What the rehearsal establishes is the same
        # thing it establishes for `minted_echo`: that the profile's DECLARED SELECTOR
        # really fires on this host's build.  Comparing an adopted rehearsal by equality
        # against a value the CLI was never told would refuse every such profile at
        # `profile_readiness_unverified` -- a refusal nobody can satisfy.
        adopt = profile.identity_binding == "adopted"
        return driver.bound_readiness_signal(
            probe["output"], minted_session_id="" if adopt else minted_session_id,
            adopt=adopt)

    def start(self, *, lease_token: str | None = None,
              auth_probe_argv: Sequence[str] | None = None,
              help_text: str | None = None, prober: Any = None,
              rehearsal: Any = None, mode_rehearsal: Any = None,
              payload: str | None = None) -> StartReceipt:
        """Preflight -> child env -> spawn -> identity bind -> ONE ledger receipt.

        The order matters at every step.  Preflight runs first so a refusal leaves no
        process at all.  The child env is CONSTRUCTED (never pruned) and asserted clean
        before the fork.  Identity binds from the child's own spawn record, so the
        ``EFFECTED`` receipt is written only once an ``execve`` is proven to have happened --
        which is exactly the distinction ``executor._recover`` already switches on.
        """
        self._child_env = env_policy.build_child_env(
            self.profile, spawn_token=self.spawn_token,
            secret_resolver=self._secret_resolver)
        # The rehearsal DEFAULTS to the real one.  It stays injectable so the deterministic
        # tests can drive the refusal and acceptance branches without a live binary, but a
        # caller that supplies nothing gets a genuine bounded spawn rather than a refusal it
        # has no way to satisfy.
        #
        # A session id minted for the REHEARSAL ALONE, never this session's: the rehearsal is
        # a different process, and reusing the real session id would let its output satisfy
        # R-B for the run that follows -- which is exactly the replay R-B exists to refuse.
        rehearsal_session_id = identity.mint_session_id(
            run_id=self.run_id, dispatch_id=self.dispatch_id, task_id=self.task_id)
        # EXTERNAL REVIEW #6.  The probe comes from the PROFILE when the caller names none,
        # which is what makes `launcher -> adapter -> session` able to authenticate an
        # existing OAuth CLI session at all: `StandaloneAdapter.start` calls
        # `run_dispatch(lease_token=...)` and has no argument to pass here, so before this
        # the only way to supply a probe was to construct a `StandaloneSession` directly --
        # below the production entry point, which is exactly what the finding says the
        # real-CLI E2E was doing.  The keyword remains for the deterministic tests that
        # drive the refusal branches; it now OVERRIDES a declaration rather than being the
        # only source of one.
        if auth_probe_argv is None:
            auth_probe_argv = self.profile.auth_probe_argv()
        outcomes = preflight_mod.run_preflight(
            self.profile, self._child_env, auth_probe_argv=auth_probe_argv,
            help_text=help_text, prober=prober,
            rehearsal=rehearsal if rehearsal is not None else self.rehearse_readiness,
            mode_rehearsal=(mode_rehearsal if mode_rehearsal is not None
                            else self.rehearse_delivery_mode),
            minted_session_id=rehearsal_session_id)
        decision = preflight_mod.compose(outcomes)
        if not decision["proceed"]:
            # Nothing was spawned, so teardown is NOT REQUIRED -- and saying so is different
            # from claiming a teardown was proven.
            self.state = "FAILED"
            self._journal(kind="REFUSED", derived_from="capture", event="evidence_unreadable",
                          state="FAILED",
                          vocabulary={"preflight": [dict(o) for o in outcomes],
                                      "reason": decision["reason"]})
            return self._receipt("failed", decision["reason"], teardown="not_required")

        # D4.2a: the DECLARED mode chooses the argv, and `launch_argv` REFUSES to compose
        # one without a prompt.  A `launch_with_prompt` dispatch with no payload would spawn
        # a process that waits for a stdin nobody will write -- the failure this whole axis
        # exists to make impossible.
        if self.profile.delivery_mode == "launch_with_prompt":
            if not payload:
                self.state = "FAILED"
                self._journal(kind="REFUSED", derived_from="driver",
                              event="evidence_unreadable", state="FAILED",
                              vocabulary={"failure_reason": "delivery_mode_mismatch",
                                          "detail": "launch_with_prompt requires the "
                                                    "payload at process creation"})
                return self._receipt("failed", "delivery_mode_mismatch",
                                     teardown="not_required")
            agent_argv = self.driver.launch_argv(session_id=self.session_id,
                                                 prompt=payload)
        else:
            agent_argv = self.driver.argv(session_id=self.session_id)
        sentinel = pty_supervisor.exit_sentinel_path(
            self.artifact_base, self.run_id, self.session_id, self.incarnation)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        spawn_target = pty_supervisor.spawn_record_path(
            self.artifact_base, self.run_id, self.intent_id, self.incarnation)
        argv_digest = identity.argv_digest(agent_argv)
        env_digest = env_policy.env_digest(self._child_env)

        # ---- D4.3e: idempotency, decided by READING before anything is written ---------
        prior = self.journal.delivery_intent_for(self.intent_id)
        if prior is not None:
            recorded = str(prior["source_vocabulary"].get("prompt_digest", ""))
            offered = drivers.prompt_digest(payload) if payload else recorded
            if recorded and offered and recorded != offered:
                # The SAME dispatch identity being retried with DIFFERENT work.  That is a
                # contract violation upstream, and running it would execute two different
                # prompts under one dispatch -- exactly what D-D.5 forbids.
                self.state = "FAILED"
                self._journal(kind="REFUSED", derived_from="driver",
                              event="evidence_unreadable", state="FAILED",
                              vocabulary={"failure_reason": "dispatch_prompt_digest_conflict",
                                          "recorded_digest": recorded,
                                          "offered_digest": offered})
                return self._receipt("failed", "dispatch_prompt_digest_conflict",
                                     teardown="not_required")
            spawned = [row for row in self.journal.rows_for(self.intent_id)
                       if row["kind"] == "SPAWN_OBSERVED"]
            if spawned:
                # An `execve` was REACHED with this exact payload, so the prompt may already
                # be executing or may have executed.  A prompt is a side-effecting action
                # against a paid API and against a worktree, so an uncollectable effect must
                # BLOCK, not repeat (DR-10).
                self.state = "FAILED"
                self._journal(kind="REFUSED", derived_from="runtime_state",
                              event="evidence_unreadable", state="FAILED",
                              vocabulary={"failure_reason": "IDEMPOTENCY_RECOVERY_BLOCKED",
                                          "detail": "a DELIVERY_INTENT and a spawn record "
                                                    "both exist for this dispatch; the "
                                                    "prompt is never re-executed"})
                return self._receipt("failed", "IDEMPOTENCY_RECOVERY_BLOCKED",
                                     teardown="not_required")

        # ---- D4.3a: the ATOMIC delivery intent, AFTER the claim and BEFORE the fork -----
        # `append_delivery_intent` returns only after `os.fsync` has returned, and RAISES on
        # any append failure -- so the `self._spawner(...)` below is structurally
        # unreachable when the intent did not reach stable storage.  This record is an
        # observation: it is not a claim, not a lease and not a fence, and `runtime_state`
        # remains the single claim authority (AC-37-20).
        try:
            self.delivery_intent = dict(drivers.make_delivery_intent(
                intent_id=self.intent_id, dispatch_id=self.dispatch_id,
                task_id=self.task_id, session_id=self.session_id,
                payload=payload or "", argv_digest=argv_digest,
                attempt_incarnation=self.incarnation,
                delivery_mode=self.profile.delivery_mode))
            self.journal.append_delivery_intent(self.delivery_intent)
        except (journal_mod.ExecutionJournal.IntentNotDurable, OSError) as exc:
            # O-3, enforced by control flow: no `fork` follows a failed intent append.
            # Teardown is `not_required` because nothing was spawned -- which is a different
            # statement from claiming a teardown was proven.
            self.state = "FAILED"
            self.delivery_intent = None
            try:
                self._journal(kind="REFUSED", derived_from="driver",
                              event="evidence_unreadable", state="FAILED",
                              vocabulary={"failure_reason": "delivery_intent_not_durable",
                                          "detail": exc.__class__.__name__})
            except OSError:
                pass          # the journal is what failed; do not mask the original refusal
            return self._receipt("failed", "delivery_intent_not_durable",
                                 teardown="not_required")

        try:
            # The agent argv goes to `execve` UNWRAPPED: no `/bin/sh -c` between the pty
            # and the agent, so the pty's foreground image is the profile's binary itself
            # and R-A leg 4 is an executable-identity equality.  The exit sentinel the
            # wrapper used to write is written by the pty session leader instead.
            session = self._spawner(
                argv=agent_argv, env=self._child_env, profile=self.profile,
                session_id=self.session_id, incarnation=self.incarnation,
                spawn_record_target=str(spawn_target), cwd=self.worktree_path,
                argv_digest=argv_digest, env_digest=env_digest,
                sentinel=str(sentinel), fence=self.fence,
                image=self._resolved_binary())
        except OSError as exc:
            self.state = "FAILED"
            self._journal(kind="REFUSED", derived_from="pty", event="evidence_unreadable",
                          state="FAILED", vocabulary={"spawn_error": str(exc)})
            return self._receipt("failed", "spawn_failed", teardown="not_required")
        self.pty = dict(session)
        # Finding 14.  The argv the kernel really loaded, kept where delivery verification
        # reads it (`self.pty["argv"]`), so the replay selector sees the same composed argv
        # preflight rehearsed rather than an empty tuple.
        self.pty.setdefault("argv", tuple(str(a) for a in agent_argv))
        self.event_log.append("spawned")

        self.record = identity.make_record(
            run_id=self.run_id, repo_id=self.repo_id,
            worktree_selector=identity.stable_worktree_selector(self.repo_id,
                                                                self.worktree_path),
            agent_id=self.agent_id, task_id=self.task_id,
            dispatch_id=self.dispatch_id,
            session_id=self.session_id, pid=int(session["pid"]),
            pgid=int(session["pgid"]), sid=int(session["sid"]),
            captured_tty=_tty_name(session["slave_name"]), pty_id=str(session["pty_id"]),
            process_incarnation=self.incarnation, host_scope="local",
            spawn_token=self.spawn_token, started_at=_now_iso(),
            argv_digest=argv_digest, env_digest=env_digest,
            created_by_this_runtime=True, resource_kind="pty_session",
            user_taken_over=False)
        self._journal(kind="EVENT", derived_from="pty", event="spawned", state="STARTING",
                      vocabulary={"pty_id": session["pty_id"], "pid": session["pid"],
                                  "captured_tty": self.record["captured_tty"],
                                  "argv_digest": argv_digest, "env_digest": env_digest,
                                  "session_digest": argv_digest,
                                  **self._terminal_provenance()})

        # -- identity bind: the CHILD's own evidence that an execve happened -------------
        probe = self._await_spawn_record()
        if probe["outcome"] != "present":
            outcome = "failed" if probe["outcome"] == "absent" else "start_unknown"
            reason = ("no_execve" if probe["outcome"] == "absent"
                      else "spawn_record_unreadable")
            teardown = self._prove_teardown()
            self.state = "FAILED" if outcome == "failed" else "LOST"
            self.lost_reason = "" if outcome == "failed" else "start_unknown"
            self._journal(kind="REFUSED", derived_from="pty", event="exit_unproven",
                          state=self.state, lost_reason=self.lost_reason,
                          vocabulary={"spawn_record": probe["outcome"],
                                      "detail": probe["detail"]})
            return self._receipt(outcome, reason, teardown=teardown)
        self.event_log.append("identity_bound")
        self._journal(kind="SPAWN_OBSERVED", derived_from="pty", event="identity_bound",
                      state="STARTING", vocabulary={"spawn_record": probe["record"] or {},
                                                    "pid": self.record["pid"],
                                                    "captured_tty": self.record["captured_tty"],
                                                    "session_digest": argv_digest,
                                                    "pty_id": str((self.pty or {}).get("pty_id", "")),
                                                    **self._terminal_provenance()})

        # -- the ONE ledger write.  CLAIMED -> EFFECTED, under the caller's lease token ---
        if self.runtime_state is not None:
            self.runtime_state.record_receipt(
                self.intent_id,
                {"intent_id": self.intent_id, "task_id": self.task_id,
                 "dispatch_id": self.dispatch_id, "external_id": self.fence},
                lease_token)
            self._journal(kind="RECEIPT_OBSERVED", derived_from="runtime_state",
                          event="identity_bound", state="STARTING",
                          vocabulary={"external_id": self.fence,
                                      "pid": self.record["pid"],
                                      "captured_tty": self.record["captured_tty"]})
        return self._receipt("ready", "", teardown="not_required")

    # -- the SUPERVISED DISPATCH: what the engine's `start` contract actually requires ----
    def run_dispatch(self, *, lease_token: str | None = None,
                     result_parser: Any = None, payload: str | None = None,
                     **start_kwargs: Any) -> dict[str, Any]:
        """Spawn, reach READY, deliver, await completion, and SETTLE.  Blocking.

        **This is the engine's real contract for ``start``, and it is not optional.**
        ``executor._settle_now`` calls ``adapter.start(intent, lease_token=...)`` and then
        immediately requires ``adapter.settlement(intent_id)`` to answer -- its own comment
        says so: *"``start`` is the long blocking call -- minutes, not milliseconds -- so the
        keeper renews the lease throughout it"*.  ``LeaseKeeper`` exists for exactly this.
        ``OrcaAdapter.start`` satisfies it by running ``run_existing_task`` to completion and
        settling the ledger before it returns; an adapter that returned once the process was
        merely spawned would make every run raise ``OUT_OF_ORDER_EVENT:settlement missing``.

        **The Coordinator's turn still owns nothing.**  Blocking is the CALLER waiting; the
        child is a ``setsid`` session leader in its own session, and the journal, the exit
        sentinel and the ledger are plain files -- so the run stays re-queryable from a
        stranger process whether or not this caller is still alive.  The two properties are
        independent and both hold.

        The settlement RESULT is parsed by the SAME ``decision_contract.parse_agent_settlement``
        the Orca and fake paths use.  That is deliberate: the result vocabulary is workflow
        policy, and a standalone-specific parser would be exactly the per-runtime divergence
        AC-37-20 forbids.
        """
        existing = self._existing_receipt()
        if existing is not None:
            return existing

        # The composed prompt, or the canonical intent when the caller supplies none.  A
        # real agent dispatch composes prose from the engine's payload; the fake and
        # scripted paths hand over the canonical intent.  Either way the digest that goes
        # into the DELIVERY_INTENT record is taken from THIS value, so what is journalled
        # is always what is handed over.
        payload = payload if payload is not None else _canonical(self.intent)
        receipt = self.start(lease_token=lease_token, payload=payload, **start_kwargs)
        if receipt["start_outcome"] != "ready":
            raise StandaloneDispatchFailed(
                f"start_{receipt['start_outcome']}", receipt["failure_reason"], receipt)

        # ---- the ONE place the declared mode changes the STEP ORDER -------------------
        # It changes *when* the payload is handed over and *which* proofs are admissible.
        # It changes nothing about how proofs are typed, ordered, journalled or fenced --
        # one framing function, one proof discipline, one journal, one identity fence and
        # one claim authority, in both modes.  And it is confined to this runtime: no
        # workflow, decision or review module reads `delivery_mode` at all.
        if self.profile.delivery_mode == "launch_with_prompt":
            # The prompt left with the `execve`, so the admission quorum no longer gates a
            # WRITE -- but it still gates every advance out of `STARTING`, and nothing is
            # accepted on weaker evidence than in the other mode.  So a quorum that does not
            # close is still a NAMED failure here and delivery is not even attempted.
            admission = self.await_ready()
            if admission["state"] != "READY":
                # ONE exception, and it is D4.3c's precedence rule rather than a loophole: a
                # typed TERMINAL record may already be on the channel.  Both installed CLIs'
                # authentication-failure legs reach one in under a second -- fast enough
                # that the process can be gone before R-A's liveness legs are ever true, so
                # the quorum cannot close for a reason that has nothing to do with what
                # happened.  Settling that run from its own named cause is strictly more
                # informative than reporting a readiness timeout, and it advances no state
                # on weaker evidence: `await_completion` still requires BOTH gates, and
                # `PROMPT_DELIVERED` is not entered on this path at all.
                if self.driver.completion_record(self.capture.transcript()) is None:
                    raise StandaloneDispatchFailed(
                        "readiness_timed_out",
                        str(admission["verdict"].get("reason", "")), receipt)
                self._journal(kind="EVENT", derived_from="capture",
                              event="evidence_unreadable", state=self.state,
                              vocabulary={"detail": "the admission quorum did not close, "
                                                    "but a typed terminal record is already "
                                                    "on the channel; the run settles from "
                                                    "its own named cause",
                                          "delivery_mode": "launch_with_prompt"})
                return self._complete(receipt, lease_token=lease_token,
                                      result_parser=result_parser)
            delivery = self.await_delivery()
            if delivery["delivery"] != "delivered_confirmed":
                if not delivery.get("terminal_record_present"):
                    raise StandaloneDispatchFailed(
                        f"delivery_{delivery['delivery']}",
                        str(delivery.get("failure_reason") or ""), receipt)
                # A typed terminal record is present: fall through so `await_completion`
                # settles it under its own NAMED cause rather than as a delivery failure.
        else:
            readiness = self.await_ready()
            if readiness["state"] != "READY":
                # TIMED_OUT is a NAMED failure, not a settlement and not a success.  Raising
                # a named error rather than returning is what keeps the engine from
                # reporting the generic `OUT_OF_ORDER_EVENT:settlement missing` for a
                # readiness timeout.
                raise StandaloneDispatchFailed(
                    "readiness_timed_out",
                    str(readiness["verdict"].get("reason", "")), receipt)
            delivery = self.send({"payload": payload})
            if delivery["delivery"] != "delivered_confirmed":
                # I-2: a proof, never the absence of a failure.  `not_observed` is NEVER
                # auto-retried -- the bytes were written before verification began.
                raise StandaloneDispatchFailed(
                    f"delivery_{delivery['delivery']}",
                    str(delivery.get("proof") or ""), receipt)

        return self._complete(receipt, lease_token=lease_token,
                              result_parser=result_parser)

    def _complete(self, receipt: Mapping[str, Any], *, lease_token: str | None,
                  result_parser: Any = None) -> dict[str, Any]:
        """Await completion and SETTLE it -- succeeded or failed alike.

        ``COMPLETED`` and ``FAILED`` are BOTH settlements and both return a settlement
        event; only a ``LOST``/``TIMED_OUT`` dispatch, which produced no verdict at all,
        raises.  Before external review #2 and #8 a failing dispatch either persisted as a
        SUCCESS (when it reached both gates) or escaped as an unhandled
        `StandaloneDispatchFailed` (when it did not) -- and the second of those killed the
        whole graph with a traceback rather than routing anywhere.
        """
        completion = self.await_completion()
        if completion["state"] not in ("COMPLETED", "FAILED"):
            # R5.  The observed exit status travels WITH the refusal.  `await_completion`
            # already resolved it (`map_exit_code`), and dropping it here is what made a
            # crash and a silent clean exit indistinguishable in the durable journal.
            raise StandaloneDispatchFailed(
                completion["state"].lower(), completion.get("lost_reason", ""),
                dict(receipt),
                exit_status=(completion.get("evidence") or {}).get("exit_status"))
        verdict = dict(completion.get("verdict") or {})
        event = self._settle(completion["evidence"], lease_token=lease_token,
                             result_parser=result_parser, verdict=verdict)
        # Finding 10.  The reported outcome is the VERDICT's, the same value `_settle` just
        # journalled -- never the completion STATE alone.  An exit 0 with no completion
        # record used to arrive here as `state=COMPLETED` (through `exit_code_map[0]`)
        # carrying `verdict.outcome=failed`, and this returned `succeeded` over a journal
        # and ledger that had just recorded FAILED.
        outcome = "succeeded" if (completion["state"] == "COMPLETED"
                                  and verdict.get("outcome") == "succeeded") else "failed"
        return {**dict(receipt), "settled": True, "event_id": event["event_id"],
                "outcome": outcome}

    def _existing_receipt(self) -> dict[str, Any] | None:
        """The receipt this intent already has, or ``None``.  Idempotency, the Orca way.

        Reads the LEDGER, not memory: a successor process holds none of this process's
        objects, and the whole point of the pre-effect claim is that it can tell "never
        started" from "may already exist" without re-running the effect.
        """
        if self.runtime_state is None:
            return None
        stored = self.runtime_state.get_receipt(self.intent_id)
        if not stored or not (stored.get("receipt") or {}).get("external_id"):
            return None
        held = stored["receipt"]
        return {"intent_id": self.intent_id,
                "session_id": str(held["external_id"]).split(":", 1)[0],
                "process_incarnation": str(held["external_id"]).partition(":")[2],
                "host_scope": "local", "pty_id": "", "captured_tty": "",
                "spawn_token": "", "start_outcome": "ready", "failure_reason": "",
                "teardown": "not_required", "reused_existing_effect": True}

    def await_completion(self) -> dict[str, Any]:
        """Bounded wait for BOTH gates, then the profile's OWN success predicate.

        Both gates, because either alone is exactly the confusion this ticket exists to
        prevent.  A structured result with no proven exit is a report from a process that
        may still be running; a proven exit with no result record is a process that ended
        without saying what it did.

        **Two external-review findings live in this method.**

        #4 -- the DEADLINE.  It waited under ``readiness_timeout_ms``, the bound on a
        process becoming READY.  A healthy agent doing several minutes of real work was
        therefore declared ``LOST/exit_status_absent`` while it was still running, and the
        only workaround was to inflate the READINESS bound.  Completion now has its own
        bound, ``completion_timeout_ms``, and readiness keeps its.

        #2 -- the VERDICT.  Reaching both gates used to mean ``COMPLETED``, full stop, and
        ``_settle`` then wrote ``outcome=succeeded`` unconditionally -- so a measured
        authentication failure (``is_error=True``, ``terminal_reason='api_error'``,
        ``rc=1``) was persisted as a success.  The driver now applies the profile's
        conjunctive predicate, and a record that fails it settles ``FAILED`` with the leg
        that refused NAMED.  ``FAILED`` is a settlement; ``LOST`` is not, and nothing here
        can produce a success from half the evidence.
        """
        deadline = self._clock() + self.profile.timeouts.completion_timeout_ms / 1000.0
        evidence = self.completion()
        while self._clock() < deadline:
            if evidence["settlement_record"] is not None and evidence["exit_proven"]:
                break
            self.pump(timeout_ms=200)
            evidence = self.completion()
        if evidence["settlement_record"] is not None and evidence["exit_proven"]:
            verdict = self.driver.completion_verdict(evidence["settlement_record"],
                                                    exit_status=evidence["exit_status"])
            self.event_log.append("exit_observed")
            if verdict["outcome"] == "succeeded":
                return {"state": "COMPLETED", "evidence": evidence, "lost_reason": "",
                        "verdict": verdict}
            if verdict["outcome"] == "failed":
                # A SETTLEMENT, not a loss: the run produced a declared terminal record and
                # a proven exit, and they say it did not succeed.  Reporting that as LOST
                # would throw away a fact the CLI stated plainly.
                self._journal(kind="EVENT", derived_from="capture",
                              event="evidence_unreadable", state=self.state,
                              vocabulary={"completion_verdict": dict(verdict),
                                          "detail": "the declared completion record does "
                                                    "not satisfy the profile's success "
                                                    "predicate"})
                return {"state": "FAILED", "evidence": evidence, "lost_reason": "",
                        "verdict": verdict}
            return {"state": "LOST", "evidence": evidence,
                    "lost_reason": lifecycle.resolve_unknown(
                        "exit_status_absent")["lost_reason"], "verdict": verdict}
        if not evidence["capture_answerable"]:
            return {"state": "LOST", "evidence": evidence,
                    "lost_reason": lifecycle.resolve_unknown("capture_truncated")["lost_reason"]}
        if evidence["settlement_record"] is None and evidence["exit_proven"]:
            # The process ended and produced no declared result record.  Its exit status is
            # the only thing left, and an unmapped code is LOST -- never `exited{0}`.
            mapped = lifecycle.map_exit_code(evidence["exit_status"],
                                             self.profile.exit_code_map)
            self.event_log.append("exit_observed")
            # Finding 10.  NO COMPLETION RECORD IS NEVER A SUCCESS.  A profile that maps
            # exit 0 to `COMPLETED` is stating what a clean exit means for a process that
            # ALSO declared its result; a process that exited 0 without declaring one is a
            # proven exit with a failed verdict -- a typed FAILED settlement, not a
            # completed one and not a loss.
            state = "FAILED" if mapped["state"] in lifecycle.SETTLED_STATES else mapped["state"]
            return {"state": state, "evidence": evidence,
                    "lost_reason": mapped.get("lost_reason", ""),
                    "verdict": {"outcome": "failed", "reason": "no_completion_record",
                                "detail": f"exit {evidence['exit_status']!r} with no "
                                          "declared result record",
                                "exit_status": evidence["exit_status"]}}
        self.event_log.append("exit_unproven")
        return {"state": "LOST", "evidence": evidence,
                "lost_reason": lifecycle.resolve_unknown(
                    "exit_status_absent")["lost_reason"]}

    def _settle(self, evidence: Mapping[str, Any], *, lease_token: str | None,
                result_parser: Any = None,
                verdict: Mapping[str, Any] | None = None) -> Any:
        """I-3's single entry edge.  The ONLY path to ``COMPLETED``/``FAILED``.

        The result is parsed by the SHARED policy parser, the event is built by
        ``contracts.make_settlement_event`` so ``validate_event`` accepts it unchanged, the
        journal records what was OBSERVED, and the ledger -- the settlement authority --
        is written under the caller's lease token.

        **What it hands the shared parser (external review #3).**  The agent's FINAL
        MESSAGE BODY, extracted by the driver from the profile-declared record -- not the
        whole line-delimited JSON event stream.  Handing over the stream meant the parser
        saw no `STATUS:`/`RESULT:` field lines and no fenced decision-gate record, so a body
        that parsed perfectly when supplied directly lost every field inside the stream that
        carries it.  The parser itself is untouched and is the same one the Orca and fake
        paths use: a standalone-specific parser, or a standalone-specific verdict policy,
        would be exactly the per-runtime divergence AC-37-20 forbids.

        **What it does with a FAILURE (external review #2 and #8).**  ``outcome`` is no
        longer the constant ``succeeded``.  A dispatch whose completion record fails the
        profile's predicate settles as a TYPED FAILED settlement, in the WORKFLOW'S OWN
        result vocabulary -- ``status=BLOCKED`` for a Worker, ``result=FAIL`` for a Reviewer
        -- so the engine's existing policy routes it (a Reviewer FAIL to the correction
        path, a Worker BLOCK to a named terminal) without a single CLI-specific branch
        anywhere above this module.
        """
        from .contracts import make_settlement_event
        parser = result_parser or _default_result_parser
        extracted = self.driver.result_body(self.capture.transcript())
        body = extracted["body"]
        result = dict(parser(_CapturedBody(
            body if body is not None else self.capture.transcript()), self.intent))
        outcome = str((verdict or {}).get("outcome") or "succeeded")
        if outcome != "succeeded":
            result = lifecycle.typed_failed_result(
                result, role=str(self.intent.get("role") or ""), verdict=verdict or {})
        target = "COMPLETED" if outcome == "succeeded" else "FAILED"
        event = make_settlement_event(self.intent, result,
                                      occurred_at="1970-01-01T00:00:00Z")

        check = lifecycle.check_transition(
            source=self.state, target=target, event="settlement_confirmed",
            log=self.event_log, evidence={"tier": "structured_stream"},
            from_settlement_predicate=True)
        if not check["allowed"]:
            raise StandaloneDispatchFailed(
                "settlement_edge_refused", "; ".join(check["violations"]), {})
        self.state = target
        self.event_log.append("settlement_confirmed")

        admitted = self.journal.admit(journal_mod.make_record(
            kind="SETTLEMENT_OBSERVED", derived_from="runtime_state",
            intent_id=self.intent_id,
            dispatch_id=self.dispatch_id, task_id=self.task_id,
            session_id=self.session_id, process_incarnation=self.incarnation,
            event="settlement_confirmed", state=target,
            outcome="succeeded" if outcome == "succeeded" else "failed",
            message_id=event["event_id"], reported_by=self.fence,
            axes=self._axes(settlement="settled", worker_resource="release",
                            process_liveness="already exited",
                            cleanup_authority="authorized"),
            source_vocabulary={"event": dict(event),
                               "exit_status": evidence.get("exit_status"),
                               "driver": self.driver.name,
                               "completion_verdict": dict(verdict or {}),
                               "result_body_source": extracted["source"],
                               "pid": (self.record or {}).get("pid"),
                               "captured_tty": (self.record or {}).get("captured_tty"),
                               **self._terminal_provenance()}),
            runtime_state=self.runtime_state)
        if admitted["outcome"] == "refused":
            raise StandaloneDispatchFailed("settlement_refused", admitted["code"], {})
        if self.runtime_state is not None:
            self.runtime_state.settle(self.intent_id, event, lease_token)
        # Finding 9.  Both gates held -- the exit is PROVEN by the fenced sentinel -- so the
        # supervisor's own resources are reclaimed here, on the normal completion path,
        # rather than left to accumulate one master fd and one zombie watcher per dispatch.
        self._reclaim(reason="settled")
        return event

    def settle_failed(self, failure: StandaloneDispatchFailed, *,
                      lease_token: str | None = None) -> dict[str, Any]:
        """Turn a NON-COMPLETION into a TYPED FAILED SETTLEMENT.  External review #8.

        ``StandaloneDispatchFailed`` names a stage and a reason -- a refused preflight, a
        readiness timeout, an unprovable delivery, a lost exit -- and it used to be RAISED
        all the way out of ``adapter.start``, through ``executor._settle_now``, through
        every graph node and out of ``run_cli`` as a traceback.  Auth expiry, an OOM kill, a
        crash or a missing result therefore killed the whole run instead of settling one
        dispatch, and nothing downstream ever saw a verdict it could route on.

        This produces the verdict.  The result is the WORKFLOW'S OWN failure vocabulary for
        the dispatch's role, so the engine's existing policy decides what happens next --
        there is no standalone branch in `routing.py`, `graph.py` or any review module, and
        none is needed.

        It is deliberately NOT a success path and it invents nothing: the state becomes
        ``FAILED`` through I-3's single entry edge, the journal records ``outcome=failed``
        with the named stage, and the evidence tier is ``raw`` -- the process exit is what
        was observed, not a structured settlement record.
        """
        from .contracts import make_settlement_event
        # ---- FINDING 1: TIMEOUT IS NOT SETTLEMENT ------------------------------------
        # Nothing below runs until the process's exit is PROVEN -- by the fenced sentinel,
        # or by the bounded interrupt ladder (terminate -> reap -> proof-of-death).  An
        # exit that cannot be proven journals a durable RETAINED state and raises; it does
        # not settle, so no subsequent work can start beside a possibly-live process.
        proof = self._prove_exit_before_settlement(stage=failure.stage)
        if not proof["proven"]:
            raise StandaloneDispatchUnsettled(
                "teardown_unproven",
                f"{self.intent_id}: the dispatch failed at {failure.stage} and its process "
                f"(pid {(self.record or {}).get('pid')}) could not be proven exited "
                f"({proof['how']}); the resource is retained and nothing is settled",
                evidence={"stage": failure.stage, "interrupt_outcome": proof["how"],
                          "pid": (self.record or {}).get("pid"),
                          "captured_tty": (self.record or {}).get("captured_tty")})
        exit_status = (failure.exit_status if failure.exit_status is not None
                       else proof.get("exit_status"))
        verdict = {"outcome": "failed", "reason": failure.reason or failure.stage,
                   "detail": str(failure), "stage": failure.stage,
                   # R5.  `None` is carried as a NAMED absence rather than dropped: a
                   # dispatch whose exit status was never observed is a different fact from
                   # one that exited 0, and the correction's three cause-specific
                   # regression tests rest on that distinction being durable.
                   "exit_status": exit_status,
                   "exit_proof": proof["how"]}
        extracted = self.driver.result_body(self.capture.transcript())
        body = extracted["body"]
        parsed = _default_result_parser(_CapturedBody(
            body if body is not None else self.capture.transcript()), self.intent)
        result = lifecycle.typed_failed_result(
            parsed, role=str(self.intent.get("role") or ""), verdict=verdict)
        event = make_settlement_event(self.intent, result,
                                      occurred_at="1970-01-01T00:00:00Z")
        check = lifecycle.check_transition(
            source=self.state, target="FAILED", event="settlement_confirmed",
            log=self.event_log, evidence={"tier": "raw"},
            from_settlement_predicate=True)
        if not check["allowed"]:      # pragma: no cover - I-3 admits this edge from any state
            raise failure
        self.state = "FAILED"
        self.event_log.append("settlement_confirmed")
        spawned = self.record is not None
        admitted = self.journal.admit(journal_mod.make_record(
            kind="SETTLEMENT_OBSERVED", derived_from="runtime_state",
            intent_id=self.intent_id, dispatch_id=self.dispatch_id, task_id=self.task_id,
            session_id=self.session_id, process_incarnation=self.incarnation,
            event="settlement_confirmed", state="FAILED", outcome="failed",
            message_id=event["event_id"], reported_by=self.fence,
            # The axes say what was PROVEN.  A spawned process reaches this line only
            # with its exit proven and its resources reclaimed, so it is `already exited`
            # and the cleanup was `authorized`; a dispatch that never spawned has no
            # process to be alive, and `disputed`/`not_authorized` states that no
            # authority ever established one.
            axes=self._axes(settlement="settled", worker_resource="release",
                            process_liveness="already exited" if spawned else "disputed",
                            cleanup_authority="authorized" if spawned else "not_authorized"),
            source_vocabulary={"event": dict(event), "driver": self.driver.name,
                               "completion_verdict": dict(verdict),
                               "result_body_source": extracted["source"],
                               "exit_status": exit_status,
                               "exit_proof": proof["how"],
                               "teardown": "proven" if spawned else "not_required",
                               "pid": (self.record or {}).get("pid"),
                               "captured_tty": (self.record or {}).get("captured_tty"),
                               **self._terminal_provenance()}),
            runtime_state=self.runtime_state)
        if admitted["outcome"] == "refused":
            # Finding 7.  The journal REFUSED this settlement by name.  The ledger is NOT
            # written: a ledger that says settled beside a journal holding no terminal row
            # is a permanent inconsistency, and `open_dispatches` would keep the dispatch
            # open against a ledger that has closed it.
            raise StandaloneDispatchUnsettled(
                "settlement_refused",
                f"{self.intent_id}: the journal refused the failed settlement "
                f"({admitted['code']}: {admitted['detail']}); the ledger is left unsettled",
                evidence={"code": admitted["code"], "stage": failure.stage})
        if self.runtime_state is not None:
            self.runtime_state.settle(self.intent_id, event, lease_token)
        return {**dict(failure.receipt or self._receipt("failed", failure.reason,
                                                        teardown="not_required")),
                "settled": True, "event_id": event["event_id"], "outcome": "failed",
                "failure_stage": failure.stage, "exit_proof": proof["how"],
                "teardown": "proven" if spawned else "not_required"}

    def _receipt(self, outcome: str, reason: str, *, teardown: str) -> StartReceipt:
        if outcome not in START_OUTCOMES:
            raise ValueError(f"start_outcome {outcome!r} is not a closed-set member")
        return {"intent_id": self.intent_id, "session_id": self.session_id,
                "process_incarnation": self.incarnation, "host_scope": "local",
                "pty_id": str((self.pty or {}).get("pty_id", "")),
                "captured_tty": str((self.record or {}).get("captured_tty", "")),
                "spawn_token": self.spawn_token, "start_outcome": outcome,
                "failure_reason": reason, "teardown": teardown}

    def _await_spawn_record(self, *, timeout_ms: int = 5000) -> dict[str, Any]:
        """THIS incarnation's spawn record, and no other's.  Finding 15.

        Scoped by incarnation, so a retry's identity bind cannot read an earlier attempt's
        record -- present on disk from a process that is not the one just forked -- and
        report `identity_bound` before its own child reached `execve`.  The record's own
        `pid` must also be the pid the exit watcher handed up: a record naming another pid
        is not this child's evidence.
        """
        deadline = self._clock() + timeout_ms / 1000.0
        probe = pty_supervisor.read_spawn_records(self.artifact_base, self.run_id,
                                                 self.intent_id,
                                                 incarnation=self.incarnation)
        while probe["outcome"] == "absent" and self._clock() < deadline:
            time.sleep(0.02)
            probe = pty_supervisor.read_spawn_records(self.artifact_base, self.run_id,
                                                     self.intent_id,
                                                     incarnation=self.incarnation)
        probe = dict(probe)
        if probe["outcome"] == "present":
            recorded_pid = (probe.get("record") or {}).get("pid")
            spawned_pid = int((self.pty or {}).get("pid") or 0)
            if spawned_pid and recorded_pid != spawned_pid:
                probe.update({"outcome": "unknown", "record": None,
                              "detail": f"spawn record for {self.incarnation!r} names pid "
                                        f"{recorded_pid!r}, not the spawned {spawned_pid}"})
        return probe

    def _prove_teardown(self) -> str:
        """A FAILED start proves its own teardown or RAISES (rule 1)."""
        if self.record is None or self.pty is None:
            return "not_required"
        # Drain BEFORE waiting.  A child with unflushed pty output cannot finish exiting
        # while nobody reads the master, and this function's whole job is to prove that it
        # did -- so without this it would raise StandaloneTeardownUnproven for a process
        # that was about to die cleanly.
        self.pump(timeout_ms=100)
        pid = int(self.record["pid"])
        reaped = False
        try:
            done, _status = os.waitpid(pid, os.WNOHANG)
            reaped = done == pid
        except OSError:
            reaped = False
        if not reaped:
            for sig in (15, 9):
                try:
                    os.kill(pid, sig)
                except OSError:
                    break
                time.sleep(0.05)
            try:
                done, _status = os.waitpid(pid, os.WNOHANG)
                reaped = done == pid
            except OSError:
                reaped = False
        esrch = False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            esrch = True
        except OSError:
            esrch = False
        snapshot = self._snapshot()
        incarnation_absent = (not snapshot.get("readable", False)
                              or pty_supervisor.row_for(snapshot, pid) is None)
        identity.prove_teardown(reaped=reaped, esrch=esrch,
                                incarnation_absent=incarnation_absent and snapshot.get(
                                    "readable", False))
        # Finding 9: a proven teardown RECLAIMS what this process holds -- the exit watcher
        # (its child, otherwise a zombie) and the pty master (otherwise a leaked fd).
        self._reclaim(reason="failed_start")
        return "proven"

    # -- finding 1 / finding 9: exit proof and resource reclamation ------------------------
    def _read_sentinel(self) -> dict[str, Any]:
        return pty_supervisor.read_exit_sentinel(
            pty_supervisor.exit_sentinel_path(self.artifact_base, self.run_id,
                                              self.session_id, self.incarnation),
            fence=self.fence)

    def _reclaim(self, *, reason: str) -> dict[str, Any]:
        """Reap the exit watcher and release the pty master.  Finding 9.

        Called ONLY after an exit is proven (a fenced sentinel, or the ladder's
        proof-of-death).  Both halves are this process's own resources -- the watcher is
        its child and the master fd is its descriptor -- so neither is an action on the
        agent, and neither needs an ownership permit.  Recorded in the journal so a
        stranger can see that the supervisor reclaimed rather than leaked.
        """
        reaped: dict[str, Any] = {"reaped": False, "status": None, "detail": "no pty"}
        if self.pty is not None:
            reaped = pty_supervisor.reap_leader(
                self.pty, timeout_ms=self.profile.timeouts.physical_exit_timeout_ms)
            self.release()
        # An EVENT, not a `RELEASED` row: `open_dispatches` closes a dispatch on RELEASED,
        # and reclaiming the supervisor's own descriptors is not the lifecycle release
        # verb -- the dispatch is closed by its SETTLEMENT, which follows.
        self._journal(kind="EVENT", derived_from="pty", event="exit_observed",
                      state=self.state,
                      lost_reason=self.lost_reason,
                      axes=self._axes(settlement="not_settled" if self.state not in
                                      lifecycle.SETTLED_STATES else "settled",
                                      worker_resource="release",
                                      process_liveness="already exited",
                                      cleanup_authority="authorized"),
                      vocabulary={"reason": reason, "leader_reaped": reaped["reaped"],
                                  "leader_pid": (self.pty or {}).get("leader_pid"),
                                  "leader_status": reaped["status"],
                                  "leader_detail": reaped["detail"],
                                  "master_fd_closed": True,
                                  "pid": (self.record or {}).get("pid"),
                                  "captured_tty": (self.record or {}).get("captured_tty"),
                                  **self._terminal_provenance()})
        return reaped

    def _prove_exit_before_settlement(self, *, stage: str) -> dict[str, Any]:
        """Bounded terminate -> reap -> exit proven, or a durable RETAINED state.  Finding 1.

        The rule this enforces: **a timed-out or otherwise non-completing dispatch is never
        recorded `settled/release` while its process may be alive.**  Before this, a
        readiness, delivery or completion deadline raised `StandaloneDispatchFailed`,
        `settle_failed` wrote `settled/release` with `process_liveness=disputed`, and the
        engine started the next dispatch -- a correction round -- in the same worktree
        beside the agent that was still running.

        Three outcomes, each proven rather than assumed:

        * the fenced exit SENTINEL already exists -> the OS-sourced exit is the proof; no
          signal is sent;
        * otherwise the four-rung INTERRUPT LADDER runs (graceful, bounded wait, force,
          proof-of-death) and returns `interrupted_confirmed`/`terminated_forced` -> proven;
        * otherwise (`exit_unproven`, `not_owned`, an unreadable table) -> NOT proven.  A
          RETAINED state is journalled -- `not_settled` / `retain` / `disputed` /
          `not_authorized`, `LOST/stop_unverified` -- nothing is settled, and the caller
          raises `StandaloneDispatchUnsettled` so the run stops as a typed BLOCKED
          terminal.  The open, unsettled journal row is what keeps every later pause and
          recovery from starting work beside the process.

        A proven exit is followed by reclamation: the watcher is reaped and the master fd
        closed (finding 9), and the journal says so.
        """
        if self.record is None or self.pty is None:
            return {"proven": True, "how": "not_required", "exit_status": None}
        self.pump(timeout_ms=50)
        sentinel = self._read_sentinel()
        how = "exit_sentinel"
        ladder: Mapping[str, Any] | None = None
        if sentinel["outcome"] != "exited":
            ladder = self.interrupt(f"dispatch_failed:{stage}")
            outcome = str(ladder["interrupt_outcome"])
            if outcome not in ("interrupted_confirmed", "terminated_forced"):
                self.state = "LOST"
                self.lost_reason = "stop_unverified"
                self._journal(kind="EVENT", derived_from="process_table",
                              event="exit_unproven", state="LOST",
                              lost_reason="stop_unverified",
                              axes=self._axes(settlement="not_settled",
                                              worker_resource="retain",
                                              process_liveness="disputed",
                                              cleanup_authority="not_authorized"),
                              vocabulary={"retained": True, "stage": stage,
                                          "interrupt_outcome": outcome,
                                          "ladder": [dict(step) for step in ladder["ladder"]],
                                          "detail": "the dispatch did not complete and its "
                                                    "process's exit could not be proven; "
                                                    "nothing is settled and the resource "
                                                    "is RETAINED by name",
                                          "pid": self.record["pid"],
                                          "captured_tty": self.record["captured_tty"],
                                          **self._terminal_provenance()})
                return {"proven": False, "how": outcome, "ladder": ladder,
                        "exit_status": None}
            how = f"interrupt_ladder:{outcome}"
            # The watcher writes the sentinel right after reaping the agent; give it the
            # physical-exit budget to land so the exit STATUS travels with the settlement.
            deadline = self._clock() + self.profile.timeouts.physical_exit_timeout_ms / 1000.0
            sentinel = self._read_sentinel()
            while sentinel["outcome"] != "exited" and self._clock() < deadline:
                time.sleep(0.02)
                sentinel = self._read_sentinel()
        reaped = self._reclaim(reason=f"{stage}:{how}")
        return {"proven": True, "how": how, "ladder": ladder,
                "exit_status": sentinel["code"] if sentinel["outcome"] == "exited" else None,
                "leader_reaped": reaped["reaped"]}

    # -- readiness -----------------------------------------------------------------------
    def pump(self, *, timeout_ms: int = 50) -> int:
        """Read whatever is available on the master fd into the bounded capture."""
        if self.pty is None:
            return 0
        fd = int(self.pty["master_fd"])
        if fd < 0:
            return 0          # already released (finding 9): nothing to read from
        read = 0
        deadline = self._clock() + timeout_ms / 1000.0
        while self._clock() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.01)
            if not ready:
                break
            try:
                chunk = os.read(fd, self.profile.capture.read_chunk)
            except OSError:
                break
            if not chunk:
                break
            self.capture.append(chunk, at=_now_iso())
            read += len(chunk)
        return read

    def readiness(self, *, deadline_expired: bool = False) -> lifecycle.ReadinessVerdict:
        """S3, over the quorum.  Reads the OS for R-A and the structured channel for R-B."""
        if self.record is None or self.pty is None:
            return {"verdict": "not_ready", "reason": "missing:liveness",
                    "quorum": {"R-A": False, "R-B": False, "R-C": True}}
        snapshot = self._snapshot()
        liveness = None
        if snapshot.get("readable", False):
            fd = int(self.pty["master_fd"])
            liveness = pty_supervisor.liveness_proof(
                self.record, snapshot=snapshot, master_fd=fd if fd >= 0 else None,
                expected_binary=self._resolved_binary())
        text = self.capture.transcript()
        # ONE identity for both binding modes: the minted value for `minted_echo`, the
        # frozen adopted value for `adopted`.  Everything downstream compares by EQUALITY
        # against it and neither knows nor cares which mode produced it -- which is what
        # keeps the lifecycle CLI-free.
        binding_id = self._binding_identity(text)
        evidence = self.driver.readiness_evidence(
            text, minted_session_id=binding_id, liveness=liveness)
        verdict = lifecycle.may_send_prompt(
            evidence, minted_session_id=binding_id,
            declared_record_types=[s.record_type
                                   for s in self.profile.readiness_records],
            structured_channel_readable=not self.capture.truncated,
            deadline_expired=deadline_expired)
        if verdict["verdict"] == "ready":
            self.event_log.append("readiness_observed")
            check = lifecycle.check_transition(
                source=self.state, target="READY", event="readiness_observed",
                log=self.event_log, evidence=evidence)
            if check["allowed"]:
                self.state = "READY"
                self._journal(kind="EVENT", derived_from="pty",
                              event="readiness_observed", state="READY",
                              axes=self._axes(settlement="not_settled",
                                              worker_resource="retain",
                                              process_liveness="live",
                                              cleanup_authority="not_authorized"),
                              vocabulary={"quorum": verdict["quorum"],
                                          "pid": self.record["pid"],
                                          "captured_tty": self.record["captured_tty"]})
        return verdict

    def _resolved_binary(self) -> str:
        import shutil
        return shutil.which(self.profile.binary,
                            path=self._child_env.get("PATH", "")) or ""

    def await_ready(self) -> dict[str, Any]:
        """Bounded readiness wait.  The deadline yields ``TIMED_OUT``, never ``READY``."""
        deadline = self._clock() + self.profile.timeouts.readiness_timeout_ms / 1000.0
        verdict = self.readiness()
        while verdict["verdict"] != "ready" and self._clock() < deadline:
            self.pump()
            verdict = self.readiness()
        if verdict["verdict"] != "ready":
            final = self.readiness(deadline_expired=True)
            self.state = "TIMED_OUT"
            self._journal(kind="EVENT", derived_from="pty", event="deadline_expired",
                          state="TIMED_OUT",
                          vocabulary={"verdict": final,
                                      "note": lifecycle.resolve_unknown(
                                          "readiness_timeout")["note"]})
            return {"state": "TIMED_OUT", "verdict": final}
        return {"state": "READY", "verdict": verdict}

    # -- delivery ------------------------------------------------------------------------
    def await_delivery(self) -> dict[str, Any]:
        """`launch_with_prompt`'s delivery gate.  A PROOF, or a NAMED non-delivery.

        The prompt left with the `execve`, so there is nothing to write and nothing to
        retry.  What remains is the only question that matters: **did the dispatched prompt
        reach model work?**  It is answered by the driver's CONJUNCTIVE selector over the
        private structured channel, never by the spawn having succeeded and never by the
        absence of a failure.

        Three outcomes, and the third is the one iteration 4 had to get right:

        * a proof arrives -> `delivery_proof_observed` -> `PROMPT_DELIVERED`;
        * a TYPED TERMINAL outcome arrives first -> return `terminal`, so `await_completion`
          settles it with its own NAMED cause.  **An authentication failure is reported as
          an authentication failure**, not as a delivery-mode mismatch and not as a timeout
          (D4.3c's precedence rule).  Both installed CLIs' measured auth legs reach a typed
          terminal record well inside the deadline -- under a second in each case -- and the
          driver is what knows which record that is;
        * neither, by the deadline -> `delivery_mode_mismatch`.  **Not** `TIMED_OUT`: the
          prompt provably left with the `execve` -- the `DELIVERY_INTENT` record and the
          spawn record's `argv_digest` prove it -- so this is not an unknown delivery, it is
          a driver whose declared proof set does not describe the CLI it is pointed at.
        """
        if self.profile.delivery_mode != "launch_with_prompt":
            raise drivers.DeliveryModeMismatch(
                f"await_delivery is the {self.profile.delivery_mode!r} driver's gate only")
        if self.delivery_intent is None:
            raise drivers.DeliveryModeMismatch(
                "no DELIVERY_INTENT was journalled for this dispatch, so there is no "
                "prompt digest to prove a delivery against")
        # Its OWN bound (external review #4): readiness, delivery and completion are three
        # questions and they no longer share one deadline.
        deadline = self._clock() + self.profile.timeouts.delivery_verify_timeout_ms / 1000.0
        argv = list(self.pty.get("argv", ())) if self.pty else []
        while True:
            self.pump()
            text = self.capture.transcript()
            intent = dict(self.delivery_intent)
            binding_id = self._binding_identity(text)
            if binding_id:
                intent["session_id"] = binding_id
            proof = self.driver.delivery_evidence(
                text, intent=intent,
                # C-3 / K-2: the record arrived on the fd THIS runtime created.  It is the
                # capture of that master fd and of nothing else, so provenance is
                # established by the OS rather than by a token in the payload.
                channel_owned=self.pty is not None,
                composed_argv=argv)
            if proof is not None:
                self.delivery_proof = dict(proof)
                self.event_log.append("delivery_proof_observed")
                check = lifecycle.check_transition(
                    source=self.state, target="PROMPT_DELIVERED",
                    event="delivery_proof_observed", log=self.event_log,
                    evidence={"tier": "structured_stream"})
                if check["allowed"]:
                    self.state = "PROMPT_DELIVERED"
                self._journal(kind="EVENT", derived_from="capture",
                              event="delivery_proof_observed", state=self.state,
                              vocabulary={"proof_class": proof["proof_class"],
                                          "record_type": proof["record_type"],
                                          "prompt_digest": proof["prompt_digest"],
                                          "delivery_mode": "launch_with_prompt"})
                return {"state": self.state, "delivery": "delivered_confirmed",
                        "proof": "agent_response", "delivery_proof": dict(proof)}
            if self.driver.completion_record(text) is not None:
                # A typed terminal outcome, ahead of any delivery proof.  Settlement is
                # `await_completion`'s job and it will NAME the cause; reporting a mode
                # mismatch here would convert an honestly failing run into a mislabelled one.
                self._journal(kind="EVENT", derived_from="capture",
                              event="delivery_unobserved", state=self.state,
                              vocabulary={"detail": "a typed terminal record arrived before "
                                                    "any delivery proof; the run settles "
                                                    "from its own named cause",
                                          "delivery_mode": "launch_with_prompt"})
                return {"state": self.state, "delivery": "not_observed",
                        "proof": None, "terminal_record_present": True}
            if self._clock() >= deadline:
                self.state = "FAILED"
                self._journal(kind="EVENT", derived_from="capture",
                              event="delivery_unobserved", state="FAILED",
                              vocabulary={"failure_reason": "delivery_mode_mismatch",
                                          "detail": "neither a delivery proof nor a typed "
                                                    "terminal outcome arrived; the prompt "
                                                    "provably left with the execve",
                                          "intent_prompt_digest":
                                              self.delivery_intent["prompt_digest"],
                                          "argv_digest":
                                              self.delivery_intent["argv_digest"]})
                return {"state": "FAILED", "delivery": "not_observed", "proof": None,
                        "failure_reason": "delivery_mode_mismatch"}

    def send(self, command: Mapping[str, Any]) -> dict[str, Any]:
        """Frame, write once, settle (UNCAPPED), Enter, then verify by one of three proofs."""
        if self.record is None or self.pty is None:
            return {"intent_id": self.intent_id, "delivery": "stale_handle",
                    "proof": None, "frame_bytes": 0, "settle_ms": 0}
        snapshot = self._snapshot()
        # Finding 16: `{}` when the table was READ and holds no such pid, `None` only when
        # it could not be read -- `verify` names the two differently.
        observed = interrupt_mod.observed_row(snapshot, int(self.record["pid"]))
        try:
            permit = identity.assert_may_act(self.record, "write_input", observed=observed)
        except identity.OwnershipRefused:
            return {"intent_id": self.intent_id, "delivery": "stale_handle",
                    "proof": None, "frame_bytes": 0, "settle_ms": 0}
        identity.require_permit(permit, self.record, "write_input")
        payload = command.get("payload") if isinstance(command, Mapping) else None
        text = payload if isinstance(payload, str) else _canonical(command)
        baseline = self.capture.size
        rate = self._measured_ingest_rate
        if rate is None:
            # Measured on a THROWAWAY pty, never on this session's: padding written into a
            # live agent's stdin would be read as input, and the write would block whenever
            # the child is not currently reading.  See `measure_ingest_rate`.
            rate = preflight_mod.measure_ingest_rate()
            self._measured_ingest_rate = rate
        result = drivers.deliver(
            int(self.pty["master_fd"]), text, profile=self.profile,
            measured_ingest_rate=rate,
            verify=lambda working: self._verify_delivery(baseline, working))
        self.event_log.append("prompt_written")
        if result["delivery"] == "delivered_confirmed":
            self.event_log.append("delivery_proof_observed")
            self.state = "PROMPT_DELIVERED"
            event = "delivery_proof_observed"
        else:
            self.event_log.append("delivery_unobserved")
            # I-2: never PROMPT_DELIVERED, never back to READY.  And NO SECOND WRITE.
            self.state = "TIMED_OUT"
            event = "delivery_unobserved"
        self._journal(kind="EVENT", derived_from="capture", event=event,
                      state=self.state,
                      vocabulary={"delivery": result["delivery"],
                                  "proof": result["proof"],
                                  "frame_bytes": result["frame_bytes"],
                                  "settle_ms": result["settle_ms"],
                                  "pid": self.record["pid"],
                                  "captured_tty": self.record["captured_tty"]})
        return {"intent_id": self.intent_id, **result}

    def _verify_delivery(self, baseline: int, baseline_working: bool) -> dict[str, Any]:
        """Poll for one of the three named proofs, bounded.

        The third proof -- the output sequence advanced -- is admissible ONLY when the agent
        was already working at baseline; otherwise advancing output is just the echo of our
        own frame.
        """
        deadline = self._clock() + \
            self.profile.timeouts.delivery_verify_timeout_ms / 1000.0
        while self._clock() < deadline:
            self.pump()
            text = self.capture.transcript(baseline)
            if classify := lifecycle.classify_refusals(text):
                return {"delivery": "blocked", "proof": None, "refusals": classify}
            if self.driver.turn_start_evidence(text) is not None:
                return {"delivery": "delivered_confirmed", "proof": "turn_start"}
            if _frame_echoed(text):
                return {"delivery": "delivered_confirmed", "proof": "screen_echo"}
            if baseline_working and self.capture.size > baseline:
                return {"delivery": "delivered_confirmed", "proof": "output_sequence"}
        return {"delivery": "not_observed", "proof": None}

    # -- status --------------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """The engine-visible status mapping.  Closed vocabularies throughout."""
        snapshot = self._snapshot()
        # `disputed` is the shared vocabulary's member for "no authority establishes this".
        # An unreadable process table is exactly that -- and it is never `already exited`.
        liveness = "disputed"
        surface = "S2"
        tier = "none"
        if snapshot.get("readable", False) and self.record is not None:
            row = pty_supervisor.row_for(snapshot, int(self.record["pid"]))
            liveness = "live" if row is not None and \
                row["tty"] == self.record["captured_tty"] else "already exited"
        text = self.capture.transcript()
        wait_evidence = self.driver.wait_evidence(text)
        state = self.state
        wait_block: dict[str, Any] | None = None
        if wait_evidence is not None:
            state = "WAITING_FOR_INPUT"
            tier = wait_evidence["tier"]
            wait_block = {"value": wait_evidence["source_vocabulary"].get("type", ""),
                          "provenance": wait_evidence["source_vocabulary"].get(
                              "provenance", "hook")}
        answerable = self.capture.completion_is_answerable()
        lost_reason = self.lost_reason
        if not answerable["answerable"] and state not in lifecycle.SETTLED_STATES:
            state = "LOST"
            lost_reason = answerable["lost_reason"]
        result: dict[str, Any] = {
            "state": state,
            "evidence": {"surface": surface, "tier": tier, "live_observed": liveness == "live"},
            "axes": self._axes(
                settlement="settled" if state in lifecycle.SETTLED_STATES else "not_settled",
                worker_resource="retain",
                process_liveness=liveness,
                cleanup_authority="not_authorized"),
            "source_vocabulary": {"driver": self.driver.name, "state": self.state,
                                  "capture": capture_mod.redacted_summary(self.capture)},
        }
        if state == "LOST":
            result["lost_reason"] = lost_reason or "evidence_unreadable"
        if wait_block is not None:
            result["wait"] = wait_block
        return result

    # -- interrupt -----------------------------------------------------------------------
    def interrupt(self, reason: str) -> dict[str, Any]:
        """The four-rung ladder, gated at every rung.  Never reports an unproven exit.

        **A REFUSAL IS NOT A TRANSITION**, and this method now obeys that in code rather
        than only in prose (external review #11).  When the ownership gates G1/G2 refuse --
        an unbound tty, a shared tty, a recycled pid, or a process table that simply could
        not be read this once -- NO signal was sent and NO lifecycle edge was taken, so:

        * ``self.state`` and ``self.lost_reason`` are untouched (they already were);
        * ``event_log`` gains NOTHING.  ``interrupt_requested`` used to be appended before
          the ladder ran, so a refused request left a request in the transition log;
        * the journal record carries the intent's EXISTING axes, unchanged.  It used to
          append an ``exit_unproven`` observation whose axes said
          ``process_liveness=unverifiable``, and `ExecutionJournal.axes_for` reads the LAST
          record that carries axes -- so one transient unreadable process table permanently
          replaced a healthy dispatch's `live`/`already exited` liveness with ignorance, and
          fed exactly the ownership BLOCK finding #5 is about.

        The refusal is still RECORDED -- an audit record, with the ladder and the named
        refusal in its source vocabulary -- because "nothing happened" and "we never asked"
        are different facts.  What it may not do is move any axis.
        """
        if self.record is None:
            return {"intent_id": self.intent_id, "reason": reason,
                    "interrupt_outcome": "not_owned", "ladder": ()}
        result = interrupt_mod.interrupt(
            self.intent_id, reason, record=self.record, profile=self.profile,
            table_reader=self._table_reader, supervisor_pid=self._supervisor_pid,
            write_hint=self._write_hint if self.profile.graceful_hint else None,
            # Drain into the CAPTURE rather than discarding: the ladder needs the pipe
            # empty so an exiting child can finish exiting, and the transcript is evidence
            # this run must keep.  One call satisfies both.
            drain=lambda: self.pump(timeout_ms=50))
        mapped = interrupt_mod.lifecycle_for(result["interrupt_outcome"])
        if mapped["state"] is None:
            # REFUSED.  No signal, no edge, no axis.  The axes written here are the ones
            # already on the journal, re-stated verbatim, so `axes_for` answers exactly what
            # it answered before this call.
            self._journal(kind="REFUSED", derived_from="process_table", event="",
                          state=self.state, lost_reason=self.lost_reason,
                          axes=dict(self.journal.axes_for(self.intent_id)),
                          vocabulary={"interrupt_outcome": result["interrupt_outcome"],
                                      "refusal": "refusal_is_not_a_transition",
                                      "ladder": [dict(step) for step in result["ladder"]],
                                      "pid": self.record["pid"],
                                      "captured_tty": self.record["captured_tty"]})
            return result
        self.event_log.append("interrupt_requested")
        self.state = mapped["state"]
        self.lost_reason = mapped["lost_reason"]
        self.event_log.append(
            "exit_observed" if mapped["state"] == "INTERRUPTED" else "exit_unproven")
        self._journal(kind="EVENT", derived_from="process_table",
                      event="exit_observed" if mapped["state"] == "INTERRUPTED"
                      else "exit_unproven",
                      state=self.state, lost_reason=self.lost_reason,
                      axes=self._axes(
                          settlement="not_settled", worker_resource="retain",
                          process_liveness="already exited" if mapped["state"] == "INTERRUPTED"
                          else "disputed",
                          cleanup_authority="not_authorized"),
                      vocabulary={"interrupt_outcome": result["interrupt_outcome"],
                                  "ladder": [dict(step) for step in result["ladder"]],
                                  "pid": self.record["pid"],
                                  "captured_tty": self.record["captured_tty"]})
        return result

    def _write_hint(self, hint: bytes, permit: Any) -> None:
        identity.require_permit(permit, self.record or {}, "signal")
        if self.pty is not None and int(self.pty.get("master_fd", -1)) >= 0:
            os.write(int(self.pty["master_fd"]), hint)

    # -- completion / release -------------------------------------------------------------
    def completion(self) -> dict[str, Any]:
        """Completion EVIDENCE.  Never a verdict -- I-3 owns the verdict."""
        sentinel = pty_supervisor.read_exit_sentinel(
            pty_supervisor.exit_sentinel_path(self.artifact_base, self.run_id,
                                              self.session_id, self.incarnation),
            fence=self.fence)
        answerable = self.capture.completion_is_answerable()
        return self.driver.completion_evidence(
            self.capture.transcript(),
            exit_status=sentinel["code"] if sentinel["outcome"] == "exited" else None,
            exit_proven=sentinel["outcome"] == "exited",
            capture_answerable=answerable["answerable"])

    def release(self) -> None:
        """Drain into the capture, then close the pty master.  The file SURVIVES (AC-37-05).

        Draining into the capture first, so the transcript is complete AND the child can
        finish exiting; `standalone_pty.release` then drains whatever arrived in between and
        closes.
        """
        if self.pty is not None and int(self.pty.get("master_fd", -1)) >= 0:
            self.pump(timeout_ms=100)
            pty_supervisor.release(self.pty)


class StandaloneRuntime:
    """One runtime per run.  Holds the sessions, the journal and the ledger binding."""

    def __init__(self, *, artifact_base: str | os.PathLike[str], run_id: str,
                 profile: StandaloneProfile, runtime_state: Any = None,
                 journal: journal_mod.ExecutionJournal | None = None,
                 session_factory: Any = None, **session_kwargs: Any) -> None:
        self.artifact_base = Path(artifact_base)
        self.run_id = run_id
        self.profile = profile
        self.runtime_state = runtime_state
        self.journal = journal or journal_mod.ExecutionJournal(artifact_base, run_id)
        self._session_factory = session_factory or StandaloneSession
        self._session_kwargs = session_kwargs
        self.sessions: dict[str, StandaloneSession] = {}

    def session_for(self, intent: Mapping[str, Any]) -> StandaloneSession:
        intent_id = str(intent.get("intent_id", ""))
        if intent_id not in self.sessions:
            self.sessions[intent_id] = self._session_factory(
                intent=intent, profile=self.profile, artifact_base=self.artifact_base,
                run_id=self.run_id, journal=self.journal,
                runtime_state=self.runtime_state, **self._session_kwargs)
        return self.sessions[intent_id]

    def session(self, intent_id: str) -> StandaloneSession:
        try:
            return self.sessions[intent_id]
        except KeyError:
            raise KeyError(
                f"no live session for {intent_id!r} in this process; a stranger process "
                "reads the run through standalone_journal.rediscover instead") from None

    def rediscover(self, *, intent_ids: Sequence[str] = ()) -> journal_mod.RunSnapshot:
        """The stranger-process read.  Holds none of a live session's objects."""
        return journal_mod.rediscover(self.run_id, self.artifact_base,
                                      runtime_state=self.runtime_state,
                                      intent_ids=intent_ids)


def _tty_name(slave_name: str) -> str:
    """The tty name as ``ps`` reports it: ``/dev/ttys001`` -> ``ttys001``."""
    name = str(slave_name)
    for prefix in ("/dev/pts/", "/dev/"):
        if name.startswith(prefix):
            return name[len(prefix):] if prefix == "/dev/" else "pts/" + name[len(prefix):]
    return name


def _canonical(command: Mapping[str, Any]) -> str:
    import json as _json
    return _json.dumps(dict(command), sort_keys=True, ensure_ascii=False)


def _frame_echoed(text: str) -> bool:
    return "\x1b[200~" in text or "<ESC>[200~" in text


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
