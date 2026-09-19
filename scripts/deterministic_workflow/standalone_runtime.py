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

import errno
import hashlib
import json
import os
import select
import subprocess
import sys
import time
import uuid
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
    for kind, stage in FAILURE_STAGE_TABLE:
        if isinstance(exc, kind):
            return stage
    return None


def _failure_stage_table() -> tuple[tuple[type[BaseException], str], ...]:
    from .standalone_profile import ProfileError
    return (
        (drivers.IdentityBindingUnverified, "identity_binding_violated"),
        (drivers.DeliveryModeMismatch, "delivery_mode_mismatch"),
        (identity.StandaloneTeardownUnproven, "teardown_unproven"),
        (identity.OwnershipRefused, "ownership_refused"),
        (pty_supervisor.ProcessTableUnreadable, "process_table_unreadable"),
        (pty_supervisor.PtyRefused, "pty_refused"),
        (journal_mod.ExecutionJournal.IntentNotDurable, "delivery_intent_not_durable"),
        # ---- round 4, finding 7: the ENVIRONMENT construction failures ----------------
        # `build_child_env` runs before preflight and RAISES on a declared credential that
        # does not resolve and on a constructed environment that would leak a forbidden
        # name to the child.  Both are production outcomes of an operator's host, not
        # programming errors, and both escaped `adapter.start` as tracebacks that left the
        # ledger CLAIMED with nothing to say why.
        (env_policy.SecretUnavailable, "secret_unavailable"),
        (env_policy.ChildEnvironmentLeak, "child_environment_leak"),
        (preflight_mod.PreflightRefused, "preflight_refused"),
        (journal_mod.JournalUnreadable, "journal_unreadable"),
        (drivers.CapabilityDeclarationError, "profile_invalid"),
        (ProfileError, "profile_invalid"),
        (identity.IdentityError, "identity_record_invalid"),
        (OSError, "os_error"),
    )


#: The CLOSED table (finding 6, round 3; extended by finding 7, round 4).  Module-level so
#: a test can assert its membership rather than probe it one exception at a time.
FAILURE_STAGE_TABLE: tuple[tuple[type[BaseException], str], ...] = _failure_stage_table()


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


def task_identity(intent: Mapping[str, Any]) -> str:
    """The standalone TASK identity of an intent: its explicit ``task_id`` when it carries
    one, else ``task:<intent_id>`` (see :attr:`StandaloneSession.task_id`).  ONE function,
    so the receipt the supervising session writes and the receipt a stranger's `lookup`
    reconstructs from the spawn record name the same task."""
    intent_id = str(intent.get("intent_id", ""))
    return str(intent.get("task_id") or f"task:{intent_id}")


def dispatch_identity(intent: Mapping[str, Any], incarnation: str) -> str:
    """The standalone DISPATCH identity of one attempt: the intent's explicit
    ``dispatch_id`` when it carries one, else ``dispatch:<intent_id>:<incarnation>`` (see
    :attr:`StandaloneSession.dispatch_id`).  Shared with `lookup` for the same reason."""
    intent_id = str(intent.get("intent_id", ""))
    return str(intent.get("dispatch_id") or f"dispatch:{intent_id}:{incarnation}")


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


#: OS-48 DESIGN §1.5.  The ONE positive finality: a verified CAPTURE FENCE on disk
#: (`os48.capture_fence.v1`) that binds the settlement boundary N and sha256(capture[0:N)) to
#: the pinned emitter's proven exit and to the finalizing owner generation.  Every other
#: `finality` value is a refusal by name.  The constant's NAME is kept from round 9 (its
#: consumers are the same); its MEANING is the fence, never a hangup or a scan.
FINALITY_CAPTURE_FINALIZED = "capture_finalized"


def _stream_is_final(drained: Mapping[str, Any]) -> bool:
    """OS-48: the ONE rule for "the authoritative boundary is proven" -- a verified fence on
    disk (:func:`standalone_capture.fence_matches`) bound to this incarnation.  ``budget``,
    ``master_unreadable``, a masterless session with a sentinel but no fence, a legacy
    os37 finalized record, a foreign fence, a mismatching fence and a boundary a successor
    cannot find in the capture are all refused by name."""
    return drained.get("finality") == FINALITY_CAPTURE_FINALIZED


#: F-010: how many unattributed fork records a residual repeats verbatim (the ledger keeps all).
_DISCOVERY_FORKS_REPORTED = 16


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
                 measured_ingest_rate: float | None = None,
                 preflight_cache: Any = None) -> None:
        self.intent = dict(intent)
        #: The RUN's preflight cache (finding 11): fingerprint -> passing cacheable
        #: outcomes.  Shared by every session of one `StandaloneRuntime`; a session built
        #: alone gets a private one and caches nothing anybody else reads.
        self._preflight_cache = preflight_cache if preflight_cache is not None else {}
        self._preflight_evidence: dict[str, Any] = {}
        self.profile = profile
        # ---- follow-up review finding 6: ABSOLUTE, resolved in the SUPERVISOR ----------
        # Every path this session mints under the artifact base -- the capture, the exit
        # sentinel, the spawn record and the dispatch-scoped `-o` result file -- is handed
        # to a child that `chdir`s into the agent's worktree before it runs.  A relative
        # artifact base therefore named one file to the child (relative to the worktree)
        # and another to this process (relative to the launcher's cwd): the driver's `-o`
        # file was written successfully and `result_body()` read `None`.  The spawn
        # already canonicalised its two paths in the parent; the session now canonicalises
        # the base itself, so no path composed from it can split between two directories.
        self.artifact_base = Path(os.path.abspath(os.fspath(artifact_base)))
        self.run_id = run_id
        self.journal = journal
        self.runtime_state = runtime_state
        #: The dispatch ROLE, carried so the journal can name who this pty session belongs
        #: to.  `pause_policy.terminal_disposition` discharges a retained resource only when
        #: its row names a role, an origin AND an owner; without those three a live,
        #: perfectly accounted standalone dispatch is `residual` and BLOCKS the pause.
        self.role = str(intent.get("role") or "")
        self.repo_id = repo_id
        self.worktree_path = worktree_path or os.getcwd()
        self.agent_id = agent_id
        self._table_reader = table_reader or pty_supervisor.read_process_table
        #: The master-side read syscall (round-8 iteration 3): a seam, like the table
        #: reader and the spawner, so a lock can drive the read-error classification of
        #: `drain_after_exit` / `pump` over a REAL pty without patching `os` globally.
        self._master_reader = os.read
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
        # ---- round 4, finding 9: the result-body file is DISPATCH-SCOPED ---------------
        # `output_last_message_path` is a profile field, i.e. one path per PROFILE, and a
        # profile serves every dispatch of a run -- sequential, concurrent, and the two
        # preflight rehearsals.  Read back as "the current result", a file the PREVIOUS
        # dispatch (or a rehearsal) wrote was parsed as this dispatch's verdict whenever
        # this dispatch's own stream carried no body.  The declared field is now an
        # OPT-IN: the path the driver composes into `-o`, reads the body from and reports
        # provenance for is minted here, under this session's own directory, named by
        # this incarnation, and can therefore never be another dispatch's file.
        self.last_message_path = ""
        scoped = profile
        if profile.output_last_message_path:
            self.last_message_path = str(
                capture_mod.capture_path(self.artifact_base, run_id,
                                         self.session_id).with_name(
                    f"last_message.{self.incarnation}.md"))
            scoped = profile.with_paths(output_last_message_path=self.last_message_path)
        self.driver = drivers.driver_for(scoped)

        self.record: dict[str, Any] | None = None
        self.pty: dict[str, Any] | None = None
        self.capture = capture_mod.BoundedCapture(
            capture_mod.capture_path(self.artifact_base, run_id, self.session_id),
            limits=profile.capture)
        self.event_log: list[str] = []
        self.state = "STARTING"
        self.lost_reason = ""
        self._child_env: dict[str, str] = {}
        #: The run-scoped auth-seed result (path + reason, never a secret byte), set by
        #: `start()` before the auth probe.  Final-Review iteration 3, B1.
        self._auth_seed_result: dict[str, Any] = {"seeded": False, "reason": "not_started",
                                                  "path": ""}
        #: The ADOPTED identity (D4.4 A-1..A-6), frozen on first acceptance and compared by
        #: EQUALITY forever after.  Empty means "not yet observed", never "any id will do".
        self.adopted_id = ""
        #: The `DELIVERY_INTENT` this dispatch journalled before its fork, or ``None``.
        self.delivery_intent: dict[str, Any] | None = None
        #: F-002 / B1: the DELIVERY EVENTS observed at the driver/capture boundary -- one
        #: per payload actually handed over -- each ``{offset, payload, transport, at}``
        #: where ``offset`` is the capture size just before the write and ``transport`` is
        #: the `EchoTransport` read AT that moment (`standalone_pty.echo_transport`: the
        #: kind -- `argv` / `pty_write` -- the framing, and the pty's live termios).
        #: `lifecycle.resolve_delivery_echo` excludes ONLY a span PROVEN to be that
        #: payload's echo under that transport, at/after that offset; anything it cannot
        #: prove is named `echo_unproven` and excludes nothing.  Empty until a prompt is
        #: delivered.
        self.delivery_events: list[dict[str, Any]] = []
        #: The `DeliveryProof` this dispatch constructed, or ``None``.  ``None`` never
        #: settles anything: D4.3c's precedence rule lets a typed terminal outcome settle a
        #: run whether or not a proof was ever constructed.
        self.delivery_proof: dict[str, Any] | None = None
        #: The exit proof this session established, once it has (finding 3 of the
        #: follow-up review).  ``None`` until then.  Securing the lifecycle twice would
        #: re-run the ladder over a reclaimed pty, so the first proof is remembered.
        self.exit_proof: dict[str, Any] | None = None
        #: OS-48 (DESIGN §1.5): the fence nonce, minted BEFORE the spawn and carried by the
        #: spawn record + the journal `spawned` row; the settlement boundary N is where the
        #: watcher's `marker_bytes(fence_nonce)` lands in the capture.
        self.fence_nonce = uuid.uuid4().hex
        #: The verified boundary once a fence is bound: {"offset_n", "marker_len", "fence"}.
        self._boundary: dict[str, Any] | None = None
        #: The release-boundary observation once the two-phase release ran.
        self._release: dict[str, Any] | None = None
        #: DESIGN §2.1: the platform evidence source's identity read at decision time (a seam:
        #: a lock injects one to model reuse / unreadable identity without a real pid).
        self._identity_reader = pty_supervisor.read_identity
        #: The delivery baseline offset (DELIVERY_INTENT): settlement records are selected
        #: over [baseline, N) only.
        self._settlement_baseline = 0
        #: OS-48 F-001: the `-o` sidecar frozen when the fence was bound (`_freeze_sidecar`).
        self._sidecar_frozen: dict[str, Any] | None = None
        self._sidecar_state: str = capture_mod.SIDECAR_STATE_NONE
        #: Round-8 iteration 2/3: how the post-exit drain ENDED (`hangup` / `budget` /
        #: `master_unreadable` / `no_master`, bytes, errno) -- recorded on the settlement
        #: row of the success AND the failure path, so the finality decision is auditable.
        self.post_exit_drain: dict[str, Any] | None = None
        #: ``True`` for a session RECONSTRUCTED from durable evidence by a stranger process
        #: (finding 1 of the follow-up review): it holds no pty master and no exit-watcher
        #: child, only the ownership record, the capture file and the sentinel path.
        self.adopted = False
        #: Whether THIS session writes the ledger's settlement.  A supervising session
        #: does, under the executor's lease token; an ADOPTED one does not -- the
        #: collecting executor holds the only live lease and writes the ledger itself
        #: (`executor._collect`), so the session records the journal row and hands the
        #: event up.  The journal's admission ladder still fences against the ledger's
        #: receipt either way.
        self._writes_ledger = True

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
        return task_identity(self.intent)

    @property
    def dispatch_id(self) -> str:
        """The standalone runtime's DISPATCH identity: this intent, this incarnation.

        A dispatch is one attempt at the task, so the incarnation is exactly what
        distinguishes them -- a retry after a failed spawn is a new dispatch of the same
        task, and the fence already says so.  Deriving it from the incarnation means the
        journal's ``dispatch_id`` and its identity fence can never disagree about which
        attempt a record belongs to, which is what S-3's stale-dispatch refusal compares.
        """
        return dispatch_identity(self.intent, self.incarnation)

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
        # Finding 4 (consolidated follow-up review of 87f6179).  A LOST row carries the
        # session's lost_reason by DEFAULT: `make_record` refuses a LOST record without
        # one, and a caller that restored the state but forgot the reason -- `adopt`
        # did, for a retained dispatch -- turned a typed unsettled outcome into a
        # ValueError traceback.  The reason is therefore part of the state, not a
        # per-call argument every writer has to remember.
        written_state = state or self.state
        if written_state == "LOST" and not lost_reason:
            lost_reason = self.lost_reason
        self.journal.append(journal_mod.make_record(
            kind=kind, derived_from=derived_from, event=event,
            state=written_state, lost_reason=lost_reason,
            intent_id=self.intent_id,
            dispatch_id=self.dispatch_id, task_id=self.task_id,
            session_id=self.session_id, process_incarnation=self.incarnation,
            axes=dict(axes or self._axes(settlement="not_settled",
                                         worker_resource="retain",
                                         process_liveness="disputed",
                                         cleanup_authority="not_authorized")),
            source_vocabulary=dict(vocabulary or {}), **extra))

    def _rehearsal_profile(self, profile: StandaloneProfile, kind: str) -> StandaloneProfile:
        """A rehearsal writes its `-o` body to ITS OWN file, never the dispatch's (finding 9)."""
        if not profile.output_last_message_path or not self.last_message_path:
            return profile
        return profile.with_paths(output_last_message_path=str(
            Path(self.last_message_path).with_name(
                f"rehearsal.{kind}.{self.incarnation}.md")))

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
        driver = drivers.driver_for(self._rehearsal_profile(profile, "mode"))
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
        driver = drivers.driver_for(self._rehearsal_profile(profile, "readiness"))
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

    def _config_home_root(self) -> str:
        """The run-scoped config-root directory this profile's auth seed lands in.

        The profile's explicit ``config_root`` when it names one, else the single
        allowlisted per-driver config-root env name present in the built child env
        (``standalone_profile.ALLOWED_CONFIG_ROOT_NAMES``).  Empty when the profile declares
        no config root at all, in which case there is nothing to seed."""
        from .standalone_profile import ALLOWED_CONFIG_ROOT_NAMES
        if self.profile.config_root:
            return self.profile.config_root
        for name in sorted(ALLOWED_CONFIG_ROOT_NAMES):
            value = self._child_env.get(name)
            if value:
                return value
        return ""

    def _seed_run_scoped_auth(self) -> dict[str, Any]:
        """Seed the profile's declared credential file into the run-scoped config root,
        BEFORE the auth probe -- the production half of B1.  A no-op (``{"seeded": False}``)
        for a profile that declares no ``auth_seed_source`` and for a driver whose class
        exposes no ``seed_auth_home``.  The driver's own :meth:`seed_auth_home` does the copy
        (``0600``); this only resolves the destination ROOT and returns the driver's result
        (a PATH and a reason, never a secret byte).  A seed that fails is not silently
        swallowed into success -- the reason rides the result and the auth probe that
        follows still refuses closed against an unseeded home."""
        if not self.profile.auth_seed_source:
            return {"seeded": False, "reason": "no_auth_seed_declared", "path": ""}
        seeder = getattr(self.driver, "seed_auth_home", None)
        if seeder is None:
            return {"seeded": False, "reason": "driver_has_no_auth_home", "path": ""}
        root = self._config_home_root()
        if not root:
            return {"seeded": False, "reason": "no_config_root_declared", "path": ""}
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as exc:
            return {"seeded": False, "reason": "config_root_unwritable",
                    "path": str(root), "detail": exc.__class__.__name__}
        return seeder(root)

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
        # ---- Final-Review iteration 3, B1: SEED the run-scoped config home ---------------
        # A profile may declare a credential file to seed into its run-scoped config root
        # (`auth_seed_source` -> `<config-root>/auth_seed_dest_name`, 0600); the per-driver
        # measurement that an EMPTY config root refuses authentication lives in the driver
        # layer.  The seed used to be paid ONLY by the R10 test harness, so a profile that
        # declared it and was launched through the production `run_workflow --adapter
        # standalone` reached its auth probe against an EMPTY config root and every dispatch
        # refused -- the exact gap the final review's recovery E2E hit.  The seed is now
        # paid on the production path, BEFORE the fingerprint and the auth probe below, so a
        # declared profile authenticates end to end and its recovery re-seeds the same root.
        # A profile that declares no seed, and a driver whose class exposes no seeding
        # method, are untouched.  Only the destination PATH is retained; never its content.
        self._auth_seed_result = self._seed_run_scoped_auth()
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
        if self.last_message_path:
            Path(self.last_message_path).parent.mkdir(parents=True, exist_ok=True)
        # ---- round 4, finding 11: the safe run-scoped checks are paid ONCE per run ------
        # Keyed on the declared profile, the resolved binary image (path, size, mtime,
        # inode) and the child environment minus the per-spawn token; a hit reuses the
        # binary/version/profile/delivery-mode outcomes -- the two rehearsals among them
        # -- and STILL runs the auth probe, which is volatile and refreshed every dispatch.
        fingerprint = preflight_mod.preflight_fingerprint(
            self.profile, self._child_env, auth_probe_argv=auth_probe_argv,
            help_text=help_text)
        held = self._preflight_cache.get(fingerprint)
        outcomes = preflight_mod.run_preflight(
            self.driver.profile, self._child_env, auth_probe_argv=auth_probe_argv,
            help_text=help_text, prober=prober,
            rehearsal=rehearsal if rehearsal is not None else self.rehearse_readiness,
            mode_rehearsal=(mode_rehearsal if mode_rehearsal is not None
                            else self.rehearse_delivery_mode),
            minted_session_id=rehearsal_session_id,
            reuse=held)
        decision = preflight_mod.compose(outcomes)
        cacheable = {o["check"]: dict(o) for o in outcomes
                     if o["check"] in preflight_mod.CACHEABLE_CHECKS
                     and o["verdict"] == "pass"}
        if len(cacheable) == len(preflight_mod.CACHEABLE_CHECKS):
            self._preflight_cache[fingerprint] = cacheable
        # Recorded on the rows that already exist for every start (the refusal row below,
        # or the `spawned` row), not as a row of its own: the journal's row sequence is a
        # locked contract and the cache decision is evidence ABOUT a start, not an event.
        self._preflight_evidence = {
            "preflight_cache": "hit" if held else "miss",
            "preflight_fingerprint": fingerprint,
            "reused_checks": sorted(o["check"] for o in outcomes
                                    if o.get("evidence", {}).get("cached")),
            "auth_check": "refreshed"}
        if not decision["proceed"]:
            # Nothing was spawned, so teardown is NOT REQUIRED -- and saying so is different
            # from claiming a teardown was proven.
            self.state = "FAILED"
            self._journal(kind="REFUSED", derived_from="capture", event="evidence_unreadable",
                          state="FAILED",
                          vocabulary={"preflight": [dict(o) for o in outcomes],
                                      "reason": decision["reason"],
                                      **self._preflight_evidence})
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
                image=self._resolved_binary(),
                fence_nonce=self.fence_nonce,
                supervisor_identity=self._self_identity(capture_mod.OWNER_SUPERVISOR),
                sidecar_path=self.last_message_path)
        except pty_supervisor.SpawnHandoffFailed as exc:
            # Round 4, finding 4.  A leader was forked and an agent MAY have reached
            # `execve`; the parent just never learned its pid.  Nothing is settled from
            # that: the retained pty and leader are enough authority to find the child
            # through its own spawn record, terminate it through the ownership ladder,
            # reap and PROVE its exit -- or to leave it durably RETAINED and unsettled.
            return self._teardown_after_handoff_failure(exc, argv_digest=argv_digest,
                                                        env_digest=env_digest)
        except OSError as exc:
            self.state = "FAILED"
            self._journal(kind="REFUSED", derived_from="pty", event="evidence_unreadable",
                          state="FAILED", vocabulary={"spawn_error": str(exc)})
            return self._receipt("failed", "spawn_failed", teardown="not_required")
        self.pty = dict(session)
        self.fence_nonce = str(self.pty.get("fence_nonce") or self.fence_nonce)
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
        # OS-48 DESIGN §2.1: a PROVISIONAL identity read of the reported pid at once (the
        # evidence source, at decision time), so a failed start whose child never reaches
        # `execve` can still be torn down through an identity-checked ladder; the child's own
        # spawn record supersedes it below and a disagreement is a refusal, never a bind.
        provisional = self._identity_reader(int(session["pid"]))
        if provisional.get("start_state") == capture_mod.EVIDENCE_FINAL:
            self.record["proc_start_ticks"] = int(provisional["start_id"])
            self.record["boot_id"] = str(provisional.get("boot_id") or "")
            self.record["evidence_source"] = pty_supervisor.evidence_source_id()
        self._journal(kind="EVENT", derived_from="pty", event="spawned", state="STARTING",
                      vocabulary={"pty_id": session["pty_id"], "pid": session["pid"],
                                  "captured_tty": self.record["captured_tty"],
                                  "argv_digest": argv_digest, "env_digest": env_digest,
                                  "session_digest": argv_digest,
                                  "fence_nonce": self.fence_nonce,
                                  "supervisor_identity": self._self_identity(capture_mod.OWNER_SUPERVISOR),
                                  "evidence_source": pty_supervisor.evidence_source_id(),
                                  **self._preflight_evidence,
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
        self._bind_start_identity(probe["record"] or {})
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
            # F-002 / B1: the prompt was handed over at process creation -- it never entered
            # the pty input queue, so the line discipline CANNOT have echoed it.  The
            # delivery event records exactly that transport (`kind="argv"`), which the
            # provenance model resolves to `echo_absent`: every captured byte is the
            # agent's, nothing is excluded, and nothing is unproven.
            if payload:
                self.delivery_events.append({
                    "offset": 0, "payload": payload,
                    "transport": pty_supervisor.echo_transport(
                        None, kind="argv", framed=False, cols=self.profile.cols),
                    "at": _now_iso()})
                self._record_delivery()                   # PR #36 finding 4 (see `send`)
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
                        _readiness_failure_reason(admission["verdict"]), receipt)
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
                    _readiness_failure_reason(readiness["verdict"]), receipt)
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
        if completion["state"] == "FAILED" and self._runtime_failure_is_not_a_verdict():
            # Round 4, finding 6.  A FAILED completion is the RUNTIME's observation -- the
            # declared error field set (auth expiry), a non-zero exit (OOM, crash), a
            # process that ended without declaring a result -- and for a Reviewer that
            # observation must not become `result: FAIL`.  It is routed as a runtime
            # failure and settled by nothing.
            raise StandaloneDispatchFailed(
                "completion_failed", str(verdict.get("reason") or "completion_failed"),
                dict(receipt),
                exit_status=(completion.get("evidence") or {}).get("exit_status"))
        event = self._settle(completion["evidence"], lease_token=lease_token,
                             result_parser=result_parser, verdict=verdict)
        # Finding 10.  The reported outcome is the VERDICT's, the same value `_settle` just
        # journalled -- never the completion STATE alone.  An exit 0 with no completion
        # record used to arrive here as `state=COMPLETED` (through `exit_code_map[0]`)
        # carrying `verdict.outcome=failed`, and this returned `succeeded` over a journal
        # and ledger that had just recorded FAILED.
        outcome = "succeeded" if (completion["state"] == "COMPLETED"
                                  and verdict.get("outcome") == "succeeded") else "failed"
        # Round-8 iteration 2: a FAILED receipt CARRIES its reason.  The dispatch receipt
        # is the start receipt plus the settlement, and the start receipt's
        # `failure_reason` is empty for a start that was `ready` -- so a failed
        # settlement used to report `outcome=failed, failure_reason=''`, the shape the CI
        # failure showed, with the typed verdict reason living only in the journal.
        return {**dict(receipt), "settled": True, "event_id": event["event_id"],
                "outcome": outcome,
                "failure_reason": ("" if outcome == "succeeded"
                                   else str(verdict.get("reason") or "completion_failed"))}

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

    # -- follow-up review finding 1: COLLECT an in-flight dispatch from durable evidence --
    def adopt(self, *, fence: str) -> dict[str, Any]:
        """Rebuild THIS session's identity and ownership record from what a crashed
        supervisor left on disk, fenced to ``fence``.  Spawns nothing.

        The evidence, in the order it is trusted:

        1. the CHILD-WRITTEN SPAWN RECORD for the fence's incarnation -- the agent's own
           pid, group, session, start identity and argv digest, written before `execve`;
        2. the JOURNAL's `spawned` / `SPAWN_OBSERVED` rows for the same fence -- the pty
           id, the captured tty, the env digest and the dispatch/task ids the supervisor
           observed;
        3. the persisted PROFILE the runtime was rebuilt from (the caller's job).

        The two must AGREE -- same pid, same session id, same argv digest -- or nothing is
        adopted: a journal that names one process and a record that names another is not
        this dispatch's evidence.  ``{"adopted": bool, "detail": str, ...}`` says which.
        """
        session_id, _, incarnation = fence.partition(":")
        if not session_id or not incarnation:
            return {"adopted": False, "detail": f"fence {fence!r} names no incarnation"}
        rows = self.journal.rows_for(self.intent_id)
        mine = [row for row in rows
                if row.get("session_id") == session_id
                and row.get("process_incarnation") == incarnation]
        spawned = next((row for row in mine if row["kind"] == "EVENT"
                        and row["event"] == "spawned"), None)
        observed = next((row for row in mine if row["kind"] == "SPAWN_OBSERVED"), None)
        if spawned is None:
            return {"adopted": False,
                    "detail": f"the journal holds no spawn observation for {fence!r}"}
        probe = pty_supervisor.read_spawn_records(self.artifact_base, self.run_id,
                                                 self.intent_id, incarnation=incarnation)
        if probe["outcome"] != "present":
            return {"adopted": False, "spawn_record": probe["outcome"],
                    "detail": f"no child-written spawn record for {fence!r}: "
                              f"{probe['detail']}"}
        record = dict(probe["record"] or {})
        vocab = dict(spawned.get("source_vocabulary") or {})
        pid = int(record.get("pid") or 0)
        if not pid or int(vocab.get("pid") or 0) != pid:
            return {"adopted": False,
                    "detail": f"the spawn record names pid {pid!r} and the journal "
                              f"{vocab.get('pid')!r}; they do not name one process"}
        if str(record.get("session_id") or "") != session_id:
            return {"adopted": False,
                    "detail": "the spawn record names another session id"}
        argv_digest = str(vocab.get("argv_digest") or "")
        if argv_digest and str(record.get("argv_digest") or "") != argv_digest:
            return {"adopted": False,
                    "detail": "the spawn record's argv digest contradicts the journal's"}
        tty = str(vocab.get("captured_tty") or "")
        pty_id = str(vocab.get("pty_id") or "")
        env_digest = str(vocab.get("env_digest") or record.get("env_digest") or "")
        if not tty or not pty_id or not argv_digest or not env_digest:
            # Nothing is invented for an ownership record: an axis the journal never
            # recorded is an axis this successor cannot verify, and the ladder must not
            # act on a record that names a value nobody observed.
            return {"adopted": False,
                    "detail": "the journal names no captured tty / pty id / argv digest / "
                              "env digest to verify ownership against"}
        # ---- identity: the fence's, not freshly minted ------------------------------
        self.session_id = session_id
        self.incarnation = incarnation
        self.adopted = True
        self._writes_ledger = False
        for row in mine:
            if row.get("task_id"):
                self.intent.setdefault("task_id", row["task_id"])
            if row.get("dispatch_id"):
                self.intent.setdefault("dispatch_id", row["dispatch_id"])
        self.last_message_path = ""
        scoped = self.profile
        if self.profile.output_last_message_path:
            self.last_message_path = str(
                capture_mod.capture_path(self.artifact_base, self.run_id,
                                         self.session_id).with_name(
                    f"last_message.{self.incarnation}.md"))
            scoped = self.profile.with_paths(output_last_message_path=self.last_message_path)
        self.driver = drivers.driver_for(scoped)
        self.capture = capture_mod.BoundedCapture(
            capture_mod.capture_path(self.artifact_base, self.run_id, self.session_id),
            limits=self.profile.capture)
        self.pty = None
        self.record = self._ownership_record(
            pid=pid, pgid=int(record.get("pgid") or pid), sid=int(record.get("sid") or pid),
            tty=tty, pty_id=pty_id, argv_digest=argv_digest, env_digest=env_digest)
        self._bind_start_identity(record)
        if not record.get("fence_nonce") and vocab.get("fence_nonce"):
            self.fence_nonce = str(vocab["fence_nonce"])
        # The state the journal last observed for this fence, so the settlement edge is
        # taken from where the crashed supervisor left off rather than from STARTING.
        last_row = next((row for row in reversed(mine) if row.get("state")), None)
        last_state = str(last_row["state"]) if last_row is not None else "STARTING"
        self.state = last_state if last_state in lifecycle.STATES else "STARTING"
        # Finding 4.  The fenced row's `lost_reason` is restored WITH its state: a
        # dispatch the crashed supervisor retained as LOST (`secure_after_unexpected`,
        # `_prove_exit_before_settlement`) is adopted as that typed LOST -- reason and
        # all -- and every journal write the adoption and collection make below carries
        # it, so the adoption records a typed unsettled outcome rather than escaping.
        # A LOST row whose reason is outside the closed vocabulary is not adopted as
        # LOST at all: `cause_unreported` is the vocabulary's own name for it.
        self.lost_reason = ""
        if self.state == "LOST":
            reason = str((last_row or {}).get("lost_reason") or "")
            self.lost_reason = reason if reason in lifecycle.LOST_REASONS else "cause_unreported"
        self.event_log = ["spawned", "identity_bound"]
        if observed is not None:
            self.event_log.append("readiness_observed")
        #: Whether the crashed supervisor PROVED the prompt delivered before it died.  For
        #: `post_ready_delivery` a dispatch with no proof can never complete -- the prompt
        #: was never written and a stranger holds no master to write it -- so `collect`
        #: does not wait a completion budget for it.
        self._delivery_proven = any(row["event"] == "delivery_proof_observed"
                                    for row in mine)
        self.delivery_intent = None
        prior = self.journal.delivery_intent_for(self.intent_id)
        if prior is not None:
            self.delivery_intent = dict(prior.get("source_vocabulary") or {})
        # OS-48 PR #36 finding 4: the settlement baseline and the delivery events' digest-only
        # provenance the live session recorded before its prompt write are restored from the
        # journal row, so this successor settles the SAME [baseline, N) with the SAME echo
        # provenance (baseline 0 / no events made the same run settle differently).
        delivery = self._restore_delivery(mine)
        self._journal(kind="EVENT", derived_from="runtime_state", event="identity_bound",
                      state=self.state,
                      vocabulary={"adopted": True, "pid": pid, "captured_tty": tty,
                                  "pty_id": pty_id, "argv_digest": argv_digest,
                                  "spawn_record": record,
                                  "settlement_baseline": int(self._settlement_baseline or 0),
                                  "delivery_events_restored": int(delivery["events"]),
                                  "delivery_provenance": ("restored" if delivery["restored"]
                                                          else delivery["reason"]),
                                  "detail": "a successor process reconstructed this "
                                            "dispatch from its durable spawn record and "
                                            "journal; nothing was spawned",
                                  **self._terminal_provenance()})
        return {"adopted": True, "detail": "", "pid": pid, "captured_tty": tty}

    def collect(self, *, lease_token: str | None = None,
                result_parser: Any = None) -> dict[str, Any]:
        """Await and SETTLE an ADOPTED dispatch.  Exactly once, never a re-spawn.

        The same completion machinery the supervising path runs, over the same evidence,
        with one prelude: a stranger cannot `waitpid` the agent, so before waiting on the
        sentinel it asks the process table (identity-fenced: pid, tty, group AND the
        kernel start identity) whether the incarnation is still there.

        * still there and ours -> wait for the sentinel under the completion bound, then
          settle from the record + exit status like any dispatch; a bound that expires
          runs the ownership ladder and settles the typed failure or RETAINS by name;
        * gone, no sentinel yet, exit watcher (the recorded session leader) alive -> the
          evidence is in flight; await it for the exit-evidence budget;
        * gone, no sentinel, no watcher -> the exit is PROVEN by the table (ESRCH, or a
          recycled pid with another start identity) and its status is a NAMED absence.
          The dispatch settles from the capture under `no_completion_record` /
          `cause_unreported` as the typed failure it is -- never as a success guessed
          from a transcript with no exit status behind it;
        * unreadable table or an unownable process -> nothing is settled; the caller
          reports the dispatch unsettled and the run stops BLOCKED.
        """
        if not self.adopted or self.record is None:
            raise StandaloneDispatchFailed("collect_without_adoption",
                                           "collect() needs an adopted session", {})
        sentinel = self._read_sentinel()
        if sentinel["outcome"] != "exited":
            snapshot = self._snapshot()
            if not snapshot.get("readable", False):
                raise StandaloneDispatchUnsettled(
                    "teardown_unproven",
                    f"{self.intent_id}: the process table for "
                    f"{self.record['captured_tty']!r} could not be read; the adopted "
                    "dispatch's liveness is unknown and nothing is settled")
            proof = pty_supervisor.exit_proven(self.record, snapshot)
            if proof["proven"]:
                # Gone before any signal.  Give a still-live watcher its budget to land
                # the sentinel, then take the table's proof as the exit proof.
                leader = int(self.record.get("sid") or 0)
                # OS-48: the watcher writes the fence marker and then the sentinel right
                # after its `waitpid`; a live watcher is given the drain bound plus a
                # margin to land it (the bound also paces the two-phase release).
                wait_ms = max(StandaloneRuntime.EXIT_EVIDENCE_BUDGET_MS,
                              self.profile.timeouts.post_exit_drain_budget_ms + 1_000)
                deadline = self._clock() + wait_ms / 1000.0
                sentinel = self._read_sentinel()
                while (sentinel["outcome"] != "exited" and leader > 0
                       and _pid_present(leader) and self._clock() < deadline):
                    time.sleep(0.01)
                    sentinel = self._read_sentinel()
                if sentinel["outcome"] != "exited":
                    self.exit_proof = {"proven": True,
                                       "how": f"process_table:{proof['reason']}",
                                       "ladder": None, "exit_status": None}
                    self._journal(kind="EVENT", derived_from="process_table",
                                  event="exit_observed", state=self.state,
                                  axes=self._axes(settlement="not_settled",
                                                  worker_resource="release",
                                                  process_liveness="already exited",
                                                  cleanup_authority="authorized"),
                                  vocabulary={"adopted": True, "exit_proof": proof,
                                              "exit_status": None,
                                              "detail": "no exit sentinel was written and "
                                                        "the incarnation is proven gone; "
                                                        "the exit status is unknown",
                                              "pid": self.record["pid"],
                                              "captured_tty": self.record["captured_tty"],
                                              **self._terminal_provenance()})
        receipt = self._receipt("ready", "", teardown="not_required")
        receipt["adopted"] = True                                   # type: ignore[typeddict-unknown-key]
        if (self.profile.delivery_mode == "post_ready_delivery"
                and not self._delivery_proven and self._read_sentinel()["outcome"] != "exited"
                and (self.exit_proof is None or not self.exit_proof["proven"])):
            # The prompt was never proven delivered and nobody can deliver it now: the
            # agent is waiting on a stdin that will never be written.  Waiting the
            # completion budget would only delay the same typed outcome; the ladder
            # terminates it, proves the exit, and the dispatch settles as the failure it
            # is (or stays RETAINED by name when the proof cannot be made).
            raise StandaloneDispatchFailed(
                "delivery_not_observed",
                "the supervisor died before the prompt was proven delivered; a successor "
                "holds no channel to deliver it", dict(receipt))
        return self._complete(receipt, lease_token=lease_token, result_parser=result_parser)

    @staticmethod
    def _positive_selection_over_bound_fence(evidence: Mapping[str, Any],
                                             boundary: "dict[str, Any] | None") -> bool:
        """True when ``evidence`` was already a POSITIVE settlement selection taken over the
        SAME verified immutable boundary that is now bound -- so a second scan of the identical
        [baseline, N) bytes would be redundant and a later reader failure in it must not erase
        the selection (run_7859f202457c F-017).

        Positive = a refusal in the boundary, or a bound completion record (a `settlement_record`
        the selector returned).  The named LOST selection outcomes (`provenance_ambiguous`,
        `provenance_unbound`, `record_scan_incomplete`, `record_framing_ambiguous`) carry no
        record and are NOT positive -- they are re-scanned as before.

        Same boundary = both the prior evidence and the now-bound fence carry a boundary with an
        equal `offset_n` AND an equal `sha256_prefix` (the fence digest).  A prior scan with no
        boundary, or over a not-yet-verified / different range, is NOT kept: the legitimate
        pre-fence supersession the double scan exists for still happens."""
        if boundary is None:
            return False
        prior_boundary = evidence.get("boundary")
        if not isinstance(prior_boundary, Mapping):
            return False
        now_fence = boundary.get("fence") or {}
        now_prefix = ((now_fence.get("boundary") or {}).get("sha256_prefix"))
        prior_fence = prior_boundary.get("fence") or {}
        prior_prefix = ((prior_fence.get("boundary") or {}).get("sha256_prefix"))
        if (prior_boundary.get("offset_n") != boundary.get("offset_n")
                or not now_prefix or now_prefix != prior_prefix):
            return False
        if evidence.get("provenance_outcome") == capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY:
            return True
        # a bound completion record the selector returned (outcome is None for a clean bind
        # and for a sole error-field refusal, both of which carry the record); the LOST
        # outcomes above all carry `settlement_record is None`.
        return evidence.get("settlement_record") is not None

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
        while True:
            if evidence["exit_proven"]:
                # ---- follow-up review finding 7: a PROVEN exit ends the wait ----------
                # Once the exit is proven no further byte can ORIGINATE from the agent,
                # so waiting the remaining budget (thirty minutes by default) could not
                # change the answer.  But -- round-8 iteration 2 -- bytes the agent wrote
                # BEFORE it exited can still be IN FLIGHT to this reader: on Linux a
                # slave write reaches the master through the tty flip-buffer work queue
                # (`flush_to_ldisc`, a kworker), so under load the watcher's `waitpid` +
                # sentinel write landed before the final `turn.completed` record was
                # readable, and the "one last drain" here was `pump(timeout_ms=100)`,
                # which breaks at the first 10 ms `select` silence -- the record was
                # never read and the F06 dispatch settled FAILED over its penultimate
                # record.  The stream's END is not silence and (OS-48) not a hangup either:
                # it is the FENCE MARKER the watcher writes through its owner-held slave
                # reference after reaping the root, so the drain reads TO THE MARKER under
                # a bound, whatever a record already present looks like: a candidate record
                # seen before the marker is not final until the marker bounds it, and a
                # record after it is a diagnostic-tail byte.
                # Round-9 item 1: this branch is reached for EVERY proven exit -- an
                # exit first observed at (or past) the completion deadline included.
                # The loop used to test the deadline first, so an exit proven exactly
                # then fell through with `post_exit_drain` unset and the finality gate
                # below never ran; the gate is TOTAL now.
                # run_7859f202457c F-017 (REVIEW_IMPLEMENTATION.md, iteration 2): the drain
                # binds/verifies the fence and lets the FIRST authoritative scan run over
                # [baseline, N).  When the fence was NOT yet bound at the initial
                # `completion()` above (the ordinary path: no boundary, or a boundary the
                # drain is about to supersede), that first scan carried no selection and the
                # post-drain scan is exactly what produces the settlement -- unchanged.  But
                # when the fence was ALREADY bound and verified before this call (a
                # production-drained/adopted session: the reviewer's pre-bound caller
                # construction) the initial `completion()` already positively selected over
                # this same immutable range; a SECOND scan of the identical bytes can only
                # repeat that result or, on a settlement-reader allocation failure, ERASE it
                # (`record_scan_incomplete`, record/refusal None).  So the redundant re-scan
                # is skipped precisely when the prior evidence already reached a positive
                # selection (a refusal, or a bound completion record) over the SAME verified
                # boundary the drain establishes (same N, same fence digest); a selected
                # refusal / completion then dominates a later reader failure, which per
                # DESIGN §1.4 R1 it must.  Any other prior evidence is superseded by the
                # post-drain scan exactly as before.
                prior = evidence
                drained = self.drain_after_exit()
                self.post_exit_drain = drained           # rides BOTH settlement rows
                if self._positive_selection_over_bound_fence(prior, self._boundary):
                    evidence = prior
                else:
                    evidence = self.completion()
                evidence["post_exit_drain"] = drained
                break
            if self._clock() >= deadline:
                break
            self.pump(timeout_ms=200)
            evidence = self.completion()
        if not evidence["capture_answerable"]:
            # ---- round-8 item 2: ANSWERABILITY GATES EVERY SETTLEMENT RECORD ----------
            # This used to sit BELOW the record+exit branch, so a parsed settlement record
            # inside a capture the capture itself declares not to be evidence -- truncated,
            # a meta that is missing / unreadable / disagrees with the bytes, a tail past
            # the declared length nobody's append intent describes (a stranger's forged
            # record) -- was still handed to the verdict and settled COMPLETED, and the
            # ledger and journal recorded a success over evidence the capture had refused.
            # The capture's own answer now comes FIRST: a record it cannot vouch for is no
            # record, whatever it parses to, and the outcome is the capture's typed LOST
            # reason (`capture_truncated` / `evidence_unreadable`, both closed-set members)
            # which `_complete` raises and `settle_failed` turns into a typed FAILED
            # settlement -- never a verdict read out of the refused bytes.
            reason = str(evidence.get("lost_reason") or "capture_truncated")
            disposition = (lifecycle.resolve_unknown("capture_truncated")
                           if reason == "capture_truncated"
                           else lifecycle.resolve_unknown("required_evidence_missing",
                                                          lost_reason=reason))
            self._journal(kind="EVENT", derived_from="capture",
                          event="evidence_unreadable", state=self.state,
                          vocabulary={"capture_answerable": False,
                                      "capture_integrity": str(
                                          evidence.get("capture_integrity") or ""),
                                      "settlement_record_present":
                                          evidence["settlement_record"] is not None,
                                      "exit_proven": bool(evidence["exit_proven"]),
                                      "detail": "the capture refuses to answer the "
                                                "completion question; any settlement "
                                                "record it holds is not evidence"})
            return {"state": "LOST", "evidence": evidence,
                    "lost_reason": disposition["lost_reason"]}
        drained = evidence.get("post_exit_drain")
        if drained is not None and not _stream_is_final(drained):
            # ---- round-8 iteration 3: SETTLEMENT REQUIRES POSITIVE STREAM FINALITY ----
            # The exit is proven and the drain ran, but it did NOT end with the hangup:
            # the bound elapsed with a slave still held open (`budget`), or the master
            # could not be read (`master_unreadable`, errno named).  Iteration 2 merely
            # recorded that and went on to evaluate whatever record the transcript held,
            # so a structured success could be authorised from a stream whose end was
            # never observed -- a record read before the end is not the final record.
            # (An ADOPTED session holds no master: its positive evidence is the verified
            # FENCE on disk -- `_fence_from_disk` -- never the sentinel alone.)
            # OS-48: the disposition is the NAMED LOST reason the drain produced
            # (`boundary_unproven`, `exit_unproven`, `fence_*`, `owner_conflict`, ...): no
            # COMPLETED state, no settlement-success row, no success ledger receipt;
            # `_complete` raises it and `settle_failed` settles the typed FAILURE with
            # the reason on the receipt.  A watcher that never writes the marker within
            # the bound is a LOST/failed dispatch, never a success.
            named = str(drained.get("outcome") or capture_mod.OUTCOME_BOUNDARY_UNPROVEN)
            if named not in lifecycle.OS48_LOST_OUTCOMES:
                named = capture_mod.OUTCOME_BOUNDARY_UNPROVEN
            disposition = lifecycle.resolve_unknown("os48_named", lost_reason=named,
                                                    detail=str(drained.get("finality_detail") or ""))
            self._journal(kind="EVENT", derived_from="pty",
                          event="evidence_unreadable", state=self.state,
                          vocabulary={"post_exit_drain": dict(drained),
                                      # Round-9: the finality found by name, and what
                                      # held the slave, so the retained-slave cause
                                      # is observable in the journal.
                                      "finality": str(drained.get("finality") or ""),
                                      "finality_detail": str(
                                          drained.get("finality_detail") or ""),
                                      "holders": dict(drained.get("holders") or {}),
                                      "settlement_record_present":
                                          evidence["settlement_record"] is not None,
                                      "exit_proven": bool(evidence["exit_proven"]),
                                      "detail": "the authoritative boundary is not proven "
                                                "(no verified fence); no settlement record "
                                                "the capture holds is inside a proven boundary"})
            return {"state": "LOST", "evidence": evidence,
                    "lost_reason": disposition["lost_reason"]}
        provenance = evidence.get("provenance_outcome")
        if provenance == capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY:
            # OS-48 R1: a refusal anywhere inside the authoritative range dominates -- a typed
            # FAILED settlement, whatever success record the range also holds.
            verdict = {"outcome": "failed", "reason": capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY,
                       "detail": str(evidence.get("refusal") or ""),
                       "exit_status": evidence["exit_status"]}
            self.event_log.append("exit_observed")
            self._journal(kind="EVENT", derived_from="capture", event="evidence_unreadable",
                          state=self.state,
                          vocabulary={"completion_verdict": dict(verdict),
                                      "detail": "a refusal inside the fenced range dominates"})
            return {"state": "FAILED", "evidence": evidence, "lost_reason": "", "verdict": verdict}
        if provenance in (capture_mod.OUTCOME_PROVENANCE_AMBIGUOUS,
                          capture_mod.OUTCOME_PROVENANCE_UNBOUND,
                          capture_mod.OUTCOME_RECORD_FRAMING_AMBIGUOUS,
                          capture_mod.OUTCOME_RECORD_SCAN_INCOMPLETE):
            disposition = lifecycle.resolve_unknown("os48_named", lost_reason=provenance)
            self._journal(kind="EVENT", derived_from="capture", event="evidence_unreadable",
                          state=self.state,
                          vocabulary={"provenance_outcome": provenance,
                                      "candidates": (evidence.get("source_vocabulary") or {}).get("candidates"),
                                      # i8 F-015: the bound an incomplete framing scan hit
                                      "scan": (evidence.get("source_vocabulary") or {}).get("scan"),
                                      "detail": "the completion record's provenance is not "
                                                "bound to this dispatch's emitter subtree"})
            return {"state": "LOST", "evidence": evidence,
                    "lost_reason": disposition["lost_reason"]}
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
        # OS-48 F-001: every extraction, parser input and fallback reads the AUTHORITATIVE
        # interval [baseline, N) and the sidecar copy frozen with the fence -- never the
        # whole transcript, which a diagnostic-tail writer can still be appending to.
        authoritative = self._authoritative_text()
        extracted = self._extract_body(authoritative)
        body = extracted["body"]
        result = dict(parser(_CapturedBody(
            body if body is not None else authoritative), self.intent))
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
                               "result_body_provenance": self._body_provenance(extracted),
                               "post_exit_drain": dict(evidence.get("post_exit_drain")
                                                       or self.post_exit_drain or {}),
                               "fence": (self._boundary or {}).get("fence"),
                               "boundary": {"offset_n": (self._boundary or {}).get("offset_n"),
                                            "baseline": self._settlement_baseline},
                               # PR #36 findings 1 / 2: the bounded range the selector read
                               # and the post-N diagnostic state, on the settlement row
                               "settlement_range": evidence.get("settlement_range"),
                               "post_boundary": evidence.get("post_boundary"),
                               "provenance_outcome": evidence.get("provenance_outcome"),
                               "diagnostic_tail_bytes": (self._release or {}).get("retained_tail_bytes"),
                               "diagnostic_tail_sha256": (self._release or {}).get("retained_tail_sha256"),
                               "diagnostic_tail_state": (self._release or {}).get("state"),
                               "holders_diagnostic": self._holders_diagnostic(),
                               "pid": (self.record or {}).get("pid"),
                               "captured_tty": (self.record or {}).get("captured_tty"),
                               **self._terminal_provenance()}),
            runtime_state=self.runtime_state)
        if admitted["outcome"] == "refused":
            raise StandaloneDispatchFailed("settlement_refused", admitted["code"], {})
        if self.runtime_state is not None and self._writes_ledger:
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
        if self._runtime_failure_is_not_a_verdict():
            raise self._runtime_failure_not_settled(verdict, proof, lease_token=lease_token)
        # OS-48 F-001: a FAILED settlement parses the same authoritative interval (or, with
        # no fence at all, nothing authoritative -- an empty body, never the mutable tail).
        authoritative = self._authoritative_text()
        extracted = self._extract_body(authoritative)
        body = extracted["body"]
        parsed = _default_result_parser(_CapturedBody(
            body if body is not None else authoritative), self.intent)
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
                               "result_body_provenance": self._body_provenance(extracted),
                               "exit_status": exit_status,
                               "exit_proof": proof["how"],
                               "post_exit_drain": dict(self.post_exit_drain or {}),
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
        if self.runtime_state is not None and self._writes_ledger:
            self.runtime_state.settle(self.intent_id, event, lease_token)
        return {**dict(failure.receipt or self._receipt("failed", failure.reason,
                                                        teardown="not_required")),
                "settled": True, "event_id": event["event_id"], "outcome": "failed",
                # Round-8 iteration 2: the typed reason rides the receipt, not only the
                # journal (a `ready` start receipt carried an empty one).
                "failure_reason": str(failure.reason or failure.stage),
                "failure_stage": failure.stage, "exit_proof": proof["how"],
                "teardown": "proven" if spawned else "not_required"}

    def _extract_body(self, authoritative: str) -> dict[str, Any]:
        """The settled body: the structured record's IN-BOUNDARY body, else nothing (the
        authoritative interval itself).  The declared `-o` sidecar is NEVER a body source
        (F-001 (b), option ii): its content cannot be bound to the stream boundary N, so it is
        refused by name (`sidecar_unproven`) and the provenance says so; every installed
        profile carries its final body on the stream (the driver's `result_body_records`)."""
        extracted = self.driver.result_body(authoritative, allow_path=False)
        if extracted["source"] == "whole_transcript" and self.last_message_path:
            extracted = dict(extracted, sidecar_refused=capture_mod.SIDECAR_STATE_UNPROVEN,
                             sidecar_presence=self._sidecar_state)
        return extracted

    def _body_provenance(self, extracted: Mapping[str, Any]) -> dict[str, Any]:
        """WHERE the settled body came from, bound to this dispatch.  Finding 9.

        A body read from the dispatch-scoped `-o` file names that file, its digest and
        size, and the session/incarnation the path was minted for; a body taken from the
        structured stream names the record.  Either way a reader of the journal can tell
        which dispatch's evidence settled this intent, and the path itself carries the
        session id and incarnation so it cannot name another dispatch's file.
        """
        provenance: dict[str, Any] = {"source": extracted["source"],
                                      "session_id": self.session_id,
                                      "process_incarnation": self.incarnation,
                                      "dispatch_id": self.dispatch_id}
        # OS-48 F-001: the interval the body was taken from and its digest, so a reader can
        # re-derive the settled body from `capture.log[baseline:N)` and nothing else.
        interval = self._authoritative_interval()
        if interval is not None:
            raw = self.capture.raw()[interval[0]:interval[1]]
            provenance["interval"] = {"baseline": interval[0], "offset_n": interval[1],
                                      "sha256": hashlib.sha256(raw).hexdigest()}
        else:
            provenance["interval"] = None
        if extracted.get("sidecar_refused"):
            provenance["sidecar_refused"] = extracted["sidecar_refused"]
            provenance["sidecar_presence"] = extracted.get("sidecar_presence")
        path = extracted.get("path")
        if path:
            provenance["path"] = str(path)
            provenance["sha256"] = extracted.get("sha256")
            provenance["bytes"] = extracted.get("bytes")
            provenance["scoped_to_this_dispatch"] = (
                self.last_message_path != "" and str(path) == self.last_message_path)
        return provenance

    def _runtime_failure_is_not_a_verdict(self) -> bool:
        return self.role in lifecycle.RUNTIME_FAILURE_NOT_A_VERDICT_ROLES

    def _runtime_failure_not_settled(self, verdict: Mapping[str, Any],
                                     proof: Mapping[str, Any], *,
                                     lease_token: str | None) -> StandaloneDispatchUnsettled:
        """Round 4, finding 6: a Reviewer's RUNTIME failure, journalled and NOT settled.

        Reached only after the process's exit is PROVEN (or was never spawned) and its
        resources reclaimed, so the run stops over nothing live.  What it writes:

        * a durable `EVENT` row naming the failure by stage, reason and exit status under
          `runtime_failure`, with axes `not_settled / release / already exited /
          authorized` -- the resource is gone and nothing is settled;
        * NO `SETTLEMENT_OBSERVED` row and NO ledger settlement: there is no verdict to
          record, and a `result: FAIL` here would be a judgement nobody made;
        * for a dispatch that never spawned, the ledger claim's lease is RELEASED so a
          successor may re-run the intent the moment the operator has fixed the host --
          the claim stays CLAIMED (nothing external exists) and is recoverable by the
          ordinary ladder (no settlement, no receipt, no spawn record -> re-run).

        The typed error it returns carries `REVIEWER_RUNTIME_FAILURE`, which the adapter
        projects onto the engine's BLOCKED terminal through `IdempotencyRecoveryError`:
        no correction Worker is dispatched, no phase iteration is charged, and
        `reviewer_result` stays `None`.
        """
        spawned = self.record is not None
        self.state = "FAILED"
        self._journal(kind="EVENT", derived_from="runtime_state", event="exit_observed",
                      state="FAILED",
                      axes=self._axes(settlement="not_settled", worker_resource="release",
                                      process_liveness="already exited" if spawned
                                      else "disputed",
                                      cleanup_authority="authorized" if spawned
                                      else "not_authorized"),
                      vocabulary={"runtime_failure": dict(verdict),
                                  "code": lifecycle.REVIEWER_RUNTIME_FAILURE,
                                  "role": self.role, "driver": self.driver.name,
                                  "exit_status": verdict.get("exit_status"),
                                  "exit_proof": proof["how"],
                                  "teardown": "proven" if spawned else "not_required",
                                  "settled": False,
                                  "detail": "a runtime/infrastructure failure of a Reviewer "
                                            "dispatch is not a review verdict; nothing is "
                                            "settled and no correction is dispatched",
                                  "pid": (self.record or {}).get("pid"),
                                  "captured_tty": (self.record or {}).get("captured_tty"),
                                  **self._terminal_provenance()})
        if not spawned and self.runtime_state is not None and lease_token:
            try:
                self.runtime_state.release(self.intent_id, lease_token)
            except Exception:  # noqa: BLE001 - the lease lapses on its own; never mask
                pass
        return StandaloneDispatchUnsettled(
            "runtime_failure",
            f"{self.intent_id}: the {self.role} dispatch failed at {verdict.get('stage')} "
            f"({verdict.get('reason')}); a runtime failure is not a review verdict, so "
            "nothing is settled and no correction is dispatched",
            code=lifecycle.REVIEWER_RUNTIME_FAILURE,
            evidence={"stage": verdict.get("stage"), "reason": verdict.get("reason"),
                      "exit_status": verdict.get("exit_status"), "exit_proof": proof["how"],
                      "role": self.role, "pid": (self.record or {}).get("pid")})

    def secure_after_unexpected(self, exc: BaseException) -> dict[str, Any]:
        """LIFECYCLE SAFETY before a programming error may leave this runtime.  Finding 3.

        `adapter.start` re-raises anything outside `failure_stage_for`'s closed table --
        a `TypeError`, a `KeyError`, a rotated lease refused inside `record_receipt` --
        and until this existed it re-raised it OVER A LIVE CHILD: the graph stopped with
        a traceback and the agent kept running in the worktree, owned by nobody, its
        dispatch neither settled nor recorded as retained.

        So from the spawn onward every exit path goes through the ownership/identity
        ladder first.  This method is that path for the unexpected ones: it proves the
        process's exit (fenced sentinel, else the four-rung ladder, else nothing) and
        reclaims the supervisor's resources when it can; when it cannot, the journal holds
        a durable RETAINED + unsettled row (`_prove_exit_before_settlement` writes it) and
        the dispatch stays OPEN, which is what blocks every later pause and execution
        beside it.  Either way NOTHING is settled -- an error nobody named is not a
        verdict -- and the row written here says so by name, so a stranger reading the run
        can tell "the supervisor crashed after securing the child" from "the child was
        abandoned".  The caller re-raises only after this returns.
        """
        if self.record is None:
            return {"proven": True, "how": "not_required", "exit_status": None}
        stage = f"unexpected_error:{type(exc).__name__}"
        proof = self._prove_exit_before_settlement(stage=stage)
        if proof["proven"] and self.state not in lifecycle.SETTLED_STATES:
            self.state = "LOST"
            self.lost_reason = "evidence_unreadable"
        self._journal(kind="EVENT", derived_from="runtime_state",
                      event="exit_observed" if proof["proven"] else "exit_unproven",
                      state=self.state, lost_reason=self.lost_reason,
                      axes=self._axes(settlement="not_settled",
                                      worker_resource="release" if proof["proven"]
                                      else "retain",
                                      process_liveness="already exited" if proof["proven"]
                                      else "disputed",
                                      cleanup_authority="authorized" if proof["proven"]
                                      else "not_authorized"),
                      vocabulary={"unexpected_error": f"{type(exc).__name__}: {exc}",
                                  "lifecycle_safety": proof["how"],
                                  "exit_status": proof.get("exit_status"),
                                  "settled": False, "retained": not proof["proven"],
                                  "detail": "a programming error escaped the dispatch; the "
                                            "process was " + ("proven exited and its "
                                            "resources reclaimed" if proof["proven"] else
                                            "NOT proven exited and is retained by name")
                                            + "; nothing is settled and the error is "
                                              "re-raised only now",
                                  "pid": self.record["pid"],
                                  "captured_tty": self.record["captured_tty"],
                                  **self._terminal_provenance()})
        return proof

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

    def _bind_start_identity(self, spawn_record: Mapping[str, Any]) -> None:
        """Carry the child's OWN start identity on the ownership record.  Finding 3.

        `standalone_identity.verify` already reads `boot_id` and `proc_start_ticks` off
        the record as OPTIONAL axes -- nothing ever wrote them.  The spawn record is
        written by the agent itself, before `execve`, from the kernel's own start time
        for its pid, so it is the one same-incarnation identity a later exit proof can
        compare a still-existing pid against.  A zero stays absent: it means the platform
        reported nothing, and an absent axis is never a matching one.
        """
        if self.record is None:
            return
        provisional = int(self.record.get("proc_start_ticks") or 0)
        recorded = int(spawn_record.get("proc_start_ticks") or 0)
        if provisional and recorded and provisional != recorded:
            # The pid we read was not the child that wrote the record: a reuse between the
            # spawn and the provisional read.  The child's OWN value is authoritative and the
            # disagreement is journalled by name; nothing is signalled from the provisional.
            self._journal(kind="EVENT", derived_from="pty", event="identity_bound",
                          state=self.state,
                          vocabulary={"identity_changed": True, "provisional_start": provisional,
                                      "recorded_start": recorded})
        for axis in ("proc_start_ticks", "boot_id", "fence_nonce", "evidence_source"):
            value = spawn_record.get(axis)
            if value:
                self.record[axis] = value
        if spawn_record.get("fence_nonce"):
            self.fence_nonce = str(spawn_record["fence_nonce"])

    def _teardown_after_handoff_failure(self, failure: pty_supervisor.SpawnHandoffFailed, *,
                                        argv_digest: str, env_digest: str) -> StartReceipt:
        """Finding 4.  Bind whatever child exists, then prove or RETAIN it.

        Three shapes, each proven rather than assumed:

        * the child's spawn record for THIS incarnation is present -> an agent reached
          `execve`.  Its pid, group and start identity come from the record; the ownership
          record is built from them and the failed-start teardown runs through the SAME
          ladder as any interruption (finding 5);
        * no spawn record, and the leader (this process's own child) is reaped -> no agent
          ever existed and nothing runs: `failed/spawn_handoff_failed`, teardown proven by
          the reap and by the empty tty;
        * no spawn record and the leader still present -> the leader is the retained
          handle: it is signalled through the ladder in the pre-topology shape (it IS the
          session), and its exit is proven or the dispatch stays RETAINED.
        """
        self.pty = dict(failure.retained_session())
        self.event_log.append("spawned")
        tty = _tty_name(failure.slave_name)
        self._journal(kind="EVENT", derived_from="pty", event="evidence_unreadable",
                      state="STARTING",
                      vocabulary={"spawn_error": str(failure), "handoff": "failed",
                                  "leader_pid": failure.leader_pid, "captured_tty": tty,
                                  "pty_id": failure.pty_id, "argv_digest": argv_digest,
                                  "env_digest": env_digest, "retained": True,
                                  **self._terminal_provenance()})
        probe = self._await_spawn_record(timeout_ms=2000)
        if probe["outcome"] == "present":
            record = probe["record"] or {}
            pid = int(record.get("pid") or 0)
            self.pty.update({"pid": pid, "pgid": int(record.get("pgid") or pid)})
            self.record = self._ownership_record(
                pid=pid, pgid=int(record.get("pgid") or pid),
                sid=int(record.get("sid") or failure.leader_pid), tty=tty,
                pty_id=failure.pty_id, argv_digest=argv_digest, env_digest=env_digest)
            self._bind_start_identity(record)
            self._journal(kind="SPAWN_OBSERVED", derived_from="pty", event="identity_bound",
                          state="STARTING",
                          vocabulary={"spawn_record": record, "pid": pid, "captured_tty": tty,
                                      "session_digest": argv_digest, "pty_id": failure.pty_id,
                                      "handoff": "failed; identity bound from the child's "
                                                 "own spawn record",
                                      **self._terminal_provenance()})
        else:
            # The leader is the only process this runtime can name.  In the pre-topology
            # shape it is pid == pgid == sid on the captured tty, which is exactly what
            # `setsid` + `TIOCSCTTY` made it, and the ladder signals descendants first.
            self.record = self._ownership_record(
                pid=failure.leader_pid, pgid=failure.leader_pid, sid=failure.leader_pid,
                tty=tty, pty_id=failure.pty_id, argv_digest=argv_digest,
                env_digest=env_digest)
            self.record["proc_start_ticks"] = pty_supervisor.proc_start_ticks(
                failure.leader_pid) or None
        teardown = self._prove_teardown(reason="spawn_handoff_failed")
        self.state = "FAILED"
        self._journal(kind="REFUSED", derived_from="pty", event="exit_observed",
                      state="FAILED",
                      vocabulary={"failure_reason": "spawn_handoff_failed",
                                  "spawn_record": probe["outcome"], "teardown": teardown,
                                  "pid": self.record["pid"], "captured_tty": tty})
        return self._receipt("failed", "spawn_handoff_failed", teardown=teardown)

    def _ownership_record(self, *, pid: int, pgid: int, sid: int, tty: str, pty_id: str,
                          argv_digest: str, env_digest: str) -> dict[str, Any]:
        return dict(identity.make_record(
            run_id=self.run_id, repo_id=self.repo_id,
            worktree_selector=identity.stable_worktree_selector(self.repo_id,
                                                                self.worktree_path),
            agent_id=self.agent_id, task_id=self.task_id, dispatch_id=self.dispatch_id,
            session_id=self.session_id, pid=pid, pgid=pgid, sid=sid, captured_tty=tty,
            pty_id=pty_id, process_incarnation=self.incarnation, host_scope="local",
            spawn_token=self.spawn_token, started_at=_now_iso(), argv_digest=argv_digest,
            env_digest=env_digest, created_by_this_runtime=True,
            resource_kind="pty_session", user_taken_over=False))

    def _prove_teardown(self, *, reason: str = "failed_start") -> str:
        """A FAILED start proves its own teardown or RAISES (rule 1).  Finding 5.

        **No bare signal.**  The old shape `waitpid`ed a grandchild (which cannot succeed:
        the agent is the watcher's child, not this process's) and then sent `SIGTERM` and
        `SIGKILL` to the remembered pid with `os.kill` -- no identity re-verification, no
        ownership decision, no permit.  During the spawn-record wait that pid can be reaped
        and recycled, and a recycled pid is a stranger.  The failed-start teardown now
        takes exactly the path an interruption takes: the fenced exit sentinel first (a
        watcher that already reaped the agent needs no signal), else the four-rung ladder
        -- every rung gated by `assert_may_act` and a fresh tty-scoped table, every signal
        through `signal_target` under a permit -- and then the exit is PROVEN by
        `exit_proven` (ESRCH, or a start-identity mismatch that proves the recycled pid
        is not ours), never by the signal having been sent.
        """
        if self.record is None or self.pty is None:
            return "not_required"
        # Drain BEFORE waiting.  A child with unflushed pty output cannot finish exiting
        # while nobody reads the master, and this function's whole job is to prove that it
        # did -- so without this it would raise StandaloneTeardownUnproven for a process
        # that was about to die cleanly.
        self.pump(timeout_ms=100)
        sentinel = self._read_sentinel()
        how = "exit_sentinel"
        if sentinel["outcome"] != "exited":
            ladder = self.interrupt(f"{reason}:teardown")
            outcome = str(ladder["interrupt_outcome"])
            how = f"interrupt_ladder:{outcome}"
            if outcome not in ("interrupted_confirmed", "terminated_forced"):
                identity.prove_teardown(reaped=False, esrch=False, incarnation_absent=False,
                                        detail=f"{how}; the resource is RETAINED")
            deadline = self._clock() + self.profile.timeouts.physical_exit_timeout_ms / 1000.0
            sentinel = self._read_sentinel()
            while sentinel["outcome"] != "exited" and self._clock() < deadline:
                time.sleep(0.02)
                sentinel = self._read_sentinel()
        snapshot = self._snapshot()
        proof = pty_supervisor.exit_proven(self.record, snapshot) \
            if snapshot.get("readable", False) else {"proven": False,
                                                    "reason": "process_table_unreadable"}
        identity.prove_teardown(
            reaped=sentinel["outcome"] == "exited",
            esrch=bool(proof["proven"]), incarnation_absent=bool(proof["proven"]),
            detail=f"{how}; exit_proven={proof['reason']}")
        # Finding 9: a proven teardown RECLAIMS what this process holds -- the exit watcher
        # (its child, otherwise a zombie) and the pty master (otherwise a leaked fd).
        self._reclaim(reason=f"{reason}:{how}")
        self.exit_proof = {"proven": True, "how": how, "ladder": None,
                           "exit_status": (sentinel["code"] if sentinel["outcome"] == "exited"
                                           else None)}
        return "proven"

    # -- finding 1 / finding 9: exit proof and resource reclamation ------------------------
    def _read_sentinel(self) -> dict[str, Any]:
        return pty_supervisor.read_exit_sentinel(
            pty_supervisor.exit_sentinel_path(self.artifact_base, self.run_id,
                                              self.session_id, self.incarnation),
            fence=self.fence)

    def _release_drain_handoff(self) -> None:
        """Signal the supervisor's end of the drain handoff (round-10 item 1), releasing a
        watcher still deferring its exit -- a byte then a close (see
        :func:`pty_supervisor._signal_drain_handoff`).  Idempotent, and safe on an adopted
        session (this process spawned nothing, so it holds no handoff end)."""
        if self.pty is not None:
            pty_supervisor._signal_drain_handoff(self.pty)

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
            # RELEASE the deferring exit watcher BEFORE reaping it: the supervisor-alive
            # watcher blocks in `_defer_for_release` (serving R / C / the control socket)
            # until release-2; the supervisor's own finalizing drain (`drain_after_exit`)
            # has already run by the time `_reclaim` is reached.
            # OS-48 DESIGN §1.8: the TWO-PHASE release runs BEFORE the watcher is released --
            # release-1 (RELEASE marker), drain to R, `release.<inc>`, release-2 (the watcher
            # closes its slave reference), read to EOF -- so no acknowledged pre-R byte is lost.
            if self._boundary is not None and self._release is None:
                try:
                    self._release_two_phase()
                except Exception as exc:  # noqa: BLE001 - never lose the reclaim to bookkeeping
                    self._release = {"outcome": capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED,
                                     "error": repr(exc)}
            self._release_drain_handoff()
            reaped = pty_supervisor.reap_leader(
                self.pty, timeout_ms=self.profile.timeouts.physical_exit_timeout_ms)
            self.release()
        # OS-48 DESIGN §2.2 (F-006): teardown ACCOUNTS for the positive membership set -- a
        # member the watcher observed that is still positively alive (its pinned start
        # identity re-read now) is the NAMED residual `descendants_unreaped`; an unreadable
        # member stays `unknown`.  No member is ever signalled here (AC-10).
        residual = self.membership_residual()
        if residual["alive"] or residual["unknown"] or residual.get("outcome"):
            self._journal(kind="EVENT", derived_from="pty", event="descendants_unreaped",
                          state=self.state, vocabulary=dict(residual))
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
                                  "membership": {k: v for k, v in residual.items() if k != "members"},
                                  "pid": (self.record or {}).get("pid"),
                                  "captured_tty": (self.record or {}).get("captured_tty"),
                                  **self._terminal_provenance()})
        return reaped

    def _members_path(self) -> bytes:
        return pty_supervisor.members_path(self.capture.path, self.incarnation)

    def membership_residual(self) -> dict[str, Any]:  # noqa: C901 - one reader, every named branch
        """DESIGN §2.2: the ledger `members.<inc>.jsonl` re-read against the kernel NOW, keyed by
        INCARNATION (pid, start_id).  ``alive`` = members whose pinned start identity still
        matches a live process (`descendants_unreaped`) AND whose recorded boot id equals this
        reader's readable boot id (the boot join, iteration-2 review F-016) AND -- on Linux --
        whose recorded pidfs inode a fresh pidfd's equals under the same proven inode model
        (i8 F-016: tick equality alone is `pid_tick_unverified`, an ``unknown`` entry, never
        alive), ``unknown`` = members whose identity is unreadable, whose boot cannot be
        joined (`boot_unjoined`), or whose lifetime binding is unproven, ``exited`` = members
        the watcher saw exit or whose pid is gone / reused / bound to a different pidfs inode.
        REVIEW_IMPLEMENTATION_iteration2 F-006: the ledger's own readability is part of the
        answer -- an unreadable / torn ledger, or a watcher that could not append every line,
        is `membership_unreadable`: the set is UNKNOWN and named, never "zero members".
        REVIEW_IMPLEMENTATION_iteration4 F-009: DISCOVERY readability is part of the answer
        too -- a watcher whose process listing failed or kept changing, or that met a live
        candidate no identity source would read, wrote `discovery_unreadable` ledger records
        and a `discovery` accounting block; the residual is then `descendants_unknown` with the
        reasons named: the positive members are reported as usual, and everything the
        watcher could not see is UNKNOWN, never "no descendants"."""
        def _read_both() -> tuple[dict[str, Any], dict[str, Any] | None]:
            ledger = pty_supervisor.read_ledger(self._members_path())
            state_path = os.fsdecode(self._members_path()) + ".state.json"
            try:
                with open(state_path, "rb") as handle:
                    accounting = json.loads(handle.read().decode("utf-8"))
            except (OSError, ValueError):
                accounting = None
            return ledger, accounting
        # REVIEW_IMPLEMENTATION_iteration3 F-006: the readable content is JOINED to the durable
        # append accounting and re-read once -- a whole-line prefix that is shorter than the
        # count the watcher appended, an accounting that changes between the reads, or an
        # accounting naming another fence is a contradiction: named `membership_unreadable`
        # with the unknown remainder, never "clean".
        ledger, accounting = _read_both()
        ledger2, accounting2 = _read_both()
        out: dict[str, Any] = {"members": 0, "alive": [], "unknown": [], "exited": [],
                               "ledger": os.fsdecode(self._members_path()),
                               "ledger_state": ledger["state"], "ledger_torn": ledger["torn"],
                               "ledger_error": ledger["error"], "outcome": None,
                               "accounting": accounting}
        if ledger["state"] != capture_mod.EVIDENCE_FINAL:
            out["outcome"] = capture_mod.OUTCOME_MEMBERSHIP_UNREADABLE
            out["unknown"] = [{"ledger": ledger["state"], "error": ledger["error"]}]
            return out
        read_count = len(ledger["records"]) + int(ledger["torn"])
        appended = int((accounting or {}).get("appended") or 0)
        changing = (ledger2["records"] != ledger["records"] or ledger2["torn"] != ledger["torn"]
                    or accounting2 != accounting)
        short = accounting is not None and read_count != appended
        if (ledger["torn"] or accounting is None or int(accounting.get("failed") or 0) > 0
                or short or changing):
            # lines the watcher could not write, a fragment, a prefix shorter than the durable
            # append count, or evidence still changing: the set is at least as large as what
            # was read -- report what is known AND name the unknown remainder
            out["outcome"] = capture_mod.OUTCOME_MEMBERSHIP_UNREADABLE
            out["unknown"].append({"ledger": "changing" if changing else "incomplete",
                                   "torn": ledger["torn"], "read": read_count, "appended": appended,
                                   "append_failed": (accounting or {}).get("failed"),
                                   "accounting": "absent" if accounting is None else "present"})
        # F-009: discovery accounting -- the state file's `discovery` block joined to the
        # ledger's own `discovery_unreadable` records (either alone is enough: a record whose
        # state write was lost, or a state count whose ledger line could not be appended).
        discovery = dict((accounting or {}).get("discovery") or {})
        discovery_records = [r for r in ledger["records"]
                             if r.get("event") == pty_supervisor.MEMBER_EVENT_DISCOVERY_UNREADABLE]
        unreadable_passes = sum(int(discovery.get(k) or 0) for k in
                                ("listing_unreadable", "listing_unstable", "candidates_unreadable",
                                 "forks_coalesced", "watch_gaps", "parents_unreadable", "unobservable",
                                 "candidates_unverified"))
        if unreadable_passes or discovery_records:
            reasons = list(discovery.get("reasons") or [])
            for r in discovery_records:
                tag = f"{r.get('kind')}:{r.get('reason')}"
                if tag not in reasons:
                    reasons.append(tag)
            pids = sorted({int(p) for r in discovery_records for p in (r.get("pids") or ())}
                          | {int(p) for p in (discovery.get("pids") or ())})
            forks = [{"parent": r.get("parent"), "evidence": r.get("evidence"), "trigger": r.get("trigger")}
                     for r in discovery_records
                     if r.get("kind") == pty_supervisor.DISCOVERY_FORK_COALESCED]
            out["unknown"].append({"discovery": "unreadable",
                                   "passes": discovery.get("passes"),
                                   "root_watch": discovery.get("root_watch"),
                                   "listing_unreadable": discovery.get("listing_unreadable"),
                                   "listing_unstable": discovery.get("listing_unstable"),
                                   "candidates_unreadable": discovery.get("candidates_unreadable"),
                                   "forks_coalesced": discovery.get("forks_coalesced"),
                                   "watch_gaps": discovery.get("watch_gaps"),
                                   "parents_unreadable": discovery.get("parents_unreadable"),
                                   "unobservable": discovery.get("unobservable"),
                                   "candidates_unverified": discovery.get("candidates_unverified", 0),
                                   "records": len(discovery_records),
                                   "reasons": reasons, "pids": pids,
                                   "forks": forks[:_DISCOVERY_FORKS_REPORTED]})
            out["discovery"] = "unreadable"
            if out["outcome"] is None:
                out["outcome"] = capture_mod.OUTCOME_DESCENDANTS_UNKNOWN
        else:
            # readable AND positively watched: the root's watch preceded its exec (P2) and no
            # fork was ever observed -- the only way a darwin dispatch is clean; on Linux the
            # subreaper (P4) is the positive mechanism and no root watch exists
            watched = discovery.get("root_watch") in ("registered", "subreaper")
            out["discovery"] = ("readable" if discovery and watched else
                                "unaccounted" if not discovery else "unwatched")
            if discovery and not watched and out["outcome"] is None:
                out["outcome"] = capture_mod.OUTCOME_DESCENDANTS_UNKNOWN
                out["unknown"].append({"discovery": "unwatched", "root_watch": discovery.get("root_watch")})
        # REVIEW_IMPLEMENTATION_iteration7 F-016: members are LIFETIMES `(pid, start_id,
        # lifetime)` -- on Linux (pid, tick) is not injective, so the watcher records a new
        # lifetime ordinal whenever a live process reappears under an exited key.  The reader
        # keeps one entry per lifetime; the exit event carries its lifetime.
        latest: dict[tuple[int, int, int], dict[str, Any]] = {}
        exited: set[tuple[int, int, int]] = set()
        for record in ledger["records"]:
            ident = record.get("identity") or {}
            key = (int(ident.get("pid") or 0), int(ident.get("start_id") or 0), int(record.get("lifetime") or 1))
            if key[0] <= 0 or record.get("event") not in (pty_supervisor.MEMBER_EVENT_OBSERVED,
                                                          pty_supervisor.MEMBER_EVENT_EXITED):
                continue
            if record.get("event") == pty_supervisor.MEMBER_EVENT_EXITED:
                exited.add(key)
            else:
                latest.setdefault(key, record)
        out["members"] = len(latest)
        agent = int((self.record or {}).get("pid") or 0)
        gone: list[dict[str, Any]] = []
        for (pid, start_id, lifetime), record in latest.items():
            entry = {"pid": pid, "start_id": start_id, "lifetime": lifetime, "role": record.get("role"),
                     "observed_via": record.get("observed_via"),
                     "fixed_object": record.get("fixed_object", "none")}
            if (pid, start_id, lifetime) in exited:
                gone.append(entry)
                continue
            observed = self._identity_reader(pid)
            entry.update({"observed_start_id": observed.get("start_id"),
                          "observed_state": observed.get("start_state")})
            siblings = [k for k in latest if k[0] == pid and k[1] == start_id and k[2] != lifetime]
            if siblings:
                entry["lifetime_siblings"] = len(siblings)
            if observed.get("start_state") == capture_mod.EVIDENCE_FINAL:
                if int(observed.get("start_id") or 0) != start_id:
                    gone.append(entry)                 # the pid was reused: that incarnation is gone
                    continue
                # REVIEW_IMPLEMENTATION_iteration2 (run_5fcd2beac376) F-016 -- the BOOT join
                # (DESIGN §2.1's boot identity axis): every binding below is boot-scoped -- a
                # start tick counts from boot, the pidfs inode counter (`pidfs_ino` /
                # `pidfs_ino_nr`, kernel/pid.c + fs/pidfs.c v6.9..v6.16) restarts on every
                # boot, darwin's start time is a wall-clock re-read -- so an equality is
                # evidence of the SAME incarnation only when the ledger's recorded boot id
                # and this reader's current boot id are BOTH readable (non-empty) AND equal.
                # Missing / unreadable on either side -> UNKNOWN by name (never alive); a
                # different boot -> conservatively UNKNOWN by name (no process outlives a
                # reboot, but the reader does not turn a boot-source disagreement into a
                # positive "gone" claim either).  Applied on every platform.
                recorded_boot = str((record.get("identity") or {}).get("boot_id") or "")
                current_boot = str(observed.get("boot_id") or "")
                boot_problem = ("boot_id:unrecorded" if not recorded_boot
                                else "boot_id:unreadable" if not current_boot
                                else "boot_id:mismatch" if recorded_boot != current_boot else "")
                if boot_problem:
                    entry["lifetime_binding"] = "boot_unjoined"
                    entry["binding_detail"] = boot_problem
                    entry["recorded_boot_id"] = recorded_boot
                    entry["observed_boot_id"] = current_boot
                    out["unknown"].append(entry)
                    if out["outcome"] is None:
                        out["outcome"] = capture_mod.OUTCOME_DESCENDANTS_UNKNOWN
                    continue
                entry["boot_joined"] = True
                # REVIEW_IMPLEMENTATION_iteration8 F-016: this reader holds NO fixed object (the
                # watcher's pidfd / kqueue died with it, or never was this process's).  What
                # binds a live process under the recorded key to the recorded LIFETIME:
                #  * darwin -- the kernel's microsecond start time re-read now
                #    (`proc_pidinfo`, DESIGN §2.1's identity axis).  NOTE_EXIT pinned only the
                #    watcher's OWN observation of the exit while it lived; it pins nothing here;
                #  * Linux -- (pid, start tick) equality is NOT injective (10 ms ticks): the
                #    binding is the member's recorded pidfs inode (`fixed_object_id`) against a
                #    fresh pidfd's (`pidfd_binding`), and ONLY under the proven non-recyclable
                #    inode model the watcher recorded with it AND this reader's kernel reports
                #    (`pidfs_lifetime_model`; run_5fcd2beac376 F-016: a 32-bit pidfs inode is
                #    recyclable): equal -> the same incarnation; different -> positively gone;
                #    no such inode on either side, or an unproven / mismatched model -> UNKNOWN
                #    by name (`pid_tick_unverified`), never alive/owned.  No signal or
                #    settlement authority is ever derived from this reader's answer (AC-10).
                if sys.platform == "darwin":
                    # N-001 (run_5fcd2beac376): a RE-READ kernel timestamp, not a fixed object
                    # this reader holds -- a diagnostic identity axis, never a lifetime
                    # guarantee and never the Linux independent-binding claim
                    entry["lifetime_binding"] = "start_microsecond"
                    entry["binding_detail"] = "reread_timestamp_not_fixed_object"
                elif sys.platform == "linux" and pid != agent:
                    binding = pty_supervisor.pidfd_binding(pid)
                    recorded = int(record.get("fixed_object_id") or 0)
                    recorded_model = str(record.get("fixed_object_model") or "")
                    proven = (bool(recorded) and recorded_model
                              and recorded_model == binding.get("model")
                              and binding["state"] == capture_mod.EVIDENCE_FINAL)
                    if binding["state"] == "absent":
                        gone.append(entry)
                        continue
                    if proven:
                        entry["binding_model"] = recorded_model
                        if int(binding["fixed_object_id"]) == recorded:
                            entry["lifetime_binding"] = "pidfs_inode"
                        else:
                            entry["lifetime_binding"] = "pidfs_inode_mismatch"
                            gone.append(entry)
                            continue
                    else:
                        entry["lifetime_binding"] = "pid_tick_unverified"
                        entry["binding_detail"] = (
                            "no_recorded_fixed_object_id" if not recorded
                            else "binding_model:unproven" if not recorded_model
                            else f"binding_model:mismatch:{recorded_model}!={binding.get('model') or 'unproven'}"
                            if recorded_model != binding.get("model")
                            else f"reader_binding:{binding['state']}")
                        out["unknown"].append(entry)
                        if out["outcome"] is None:
                            out["outcome"] = capture_mod.OUTCOME_DESCENDANTS_UNKNOWN
                        continue
                (out["alive"] if pid != agent else out["unknown"]).append(entry)
            elif observed.get("start_state") == "absent":
                gone.append(entry)
            else:
                out["unknown"].append(entry)
        out["exited"] = sorted({e["pid"] for e in gone})
        out["exited_incarnations"] = gone
        return out

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
        if self.record is None or (self.pty is None and not self.adopted):
            return {"proven": True, "how": "not_required", "exit_status": None}
        if self.exit_proof is not None:
            # Already proven -- and the pty already reclaimed.  The ladder is not re-run
            # over a released master; the proof that was made is the proof.
            return dict(self.exit_proof)
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
        self.exit_proof = {"proven": True, "how": how, "ladder": ladder,
                           "exit_status": (sentinel["code"] if sentinel["outcome"] == "exited"
                                           else None),
                           "leader_reaped": reaped["reaped"]}
        return dict(self.exit_proof)

    # -- readiness -----------------------------------------------------------------------
    #: Round-8 iteration 2 / OS-48.  The DEFAULT bound on reading the pty stream TO THE FENCE
    #: MARKER after the agent's exit is proven; the operative bound is the profile's
    #: ``timeouts.post_exit_drain_budget_ms`` (same default).
    POST_EXIT_DRAIN_BUDGET_MS = 2_000

    def drain_after_exit(self, *, budget_ms: int | None = None) -> dict[str, Any]:
        """OS-48 DESIGN §1.6: read the master into the capture until the FENCE MARKER for this
        incarnation is in the capture (the positive boundary), bounded; then publish the fence.

        ``{"bytes", "ended": "marker" | "budget" | "master_unreadable" | "no_master" |
        "marker_inconsistent", "errno", "offset_n", "marker_len", "finality",
        "finality_detail", "fence"}``.  Silence never ends the drain; a hangup (EOF / EIO)
        before the marker is ``master_unreadable`` by name (the owner-held slave reference
        makes it impossible on the normal path -- probe_d1); nothing here consults any process
        or descriptor enumeration.  A masterless (adopted) session reads the fence from disk
        (:meth:`_fence_from_disk`) instead.
        """
        if self._boundary is not None:
            # PR #36 finding 1: a fence this session already bound is a property of the fence
            # file and of [0, N) -- re-verified over exactly those bytes (`fence_matches`
            # digests the prefix) and never by re-scanning the whole capture for the marker:
            # the diagnostic tail appended since is not read by a settlement path at all.
            existing = capture_mod.read_capture_fence(self._fence_path(), fence=self.fence)
            return self._bind_fence({"bytes": 0, "ended": "marker", "errno": "",
                                     "offset_n": int(self._boundary["offset_n"]),
                                     "marker_len": int(self._boundary.get("marker_len") or 0)},
                                    existing, self._read_sentinel())
        if self.pty is None or int(self.pty["master_fd"]) < 0:
            return self._fence_from_disk({"bytes": 0, "ended": "no_master", "errno": ""})
        fd = int(self.pty["master_fd"])
        budget = (self.profile.timeouts.post_exit_drain_budget_ms if budget_ms is None
                  else budget_ms) / 1000.0
        deadline = self._clock() + budget
        read = 0
        nonce = str(self.fence_nonce or "")

        def _unreadable(exc: BaseException) -> dict[str, Any]:
            code = getattr(exc, "errno", None)
            name = (errno.errorcode.get(code, str(code)) if code is not None
                    else type(exc).__name__)
            return {"bytes": read, "ended": "master_unreadable", "errno": name}

        def _boundary() -> dict[str, Any] | None:
            data = self.capture.raw()
            offset_n, marker_len, state = capture_mod.marker_span(data, nonce) if nonce else (-1, 0, capture_mod.EVIDENCE_UNKNOWN)
            if state == capture_mod.EVIDENCE_FINAL:
                return {"bytes": read, "ended": "marker", "errno": "", "offset_n": offset_n,
                        "marker_len": marker_len}
            if state == capture_mod.EVIDENCE_INCONSISTENT:
                return {"bytes": read, "ended": "marker_inconsistent", "errno": ""}
            return None

        found = _boundary()                              # the marker may already be captured
        if found is not None:
            return self._publish_fence(found)
        while True:
            now = self._clock()
            if now >= deadline:
                return self._publish_fence({"bytes": read, "ended": "budget", "errno": ""})
            try:
                ready, _, _ = select.select([fd], [], [], min(0.05, deadline - now))
            except InterruptedError:
                continue
            except (OSError, ValueError) as exc:
                return self._publish_fence(_unreadable(exc))
            if not ready:
                continue
            try:
                chunk = self._master_reader(fd, self.profile.capture.read_chunk)
            except InterruptedError:
                continue
            except OSError as exc:
                if exc.errno == errno.EIO:
                    return self._publish_fence({"bytes": read, "ended": "master_unreadable",
                                                "errno": "EIO"})
                return self._publish_fence(_unreadable(exc))
            if not chunk:
                return self._publish_fence({"bytes": read, "ended": "master_unreadable",
                                            "errno": "EOF"})
            self.capture.append(chunk, at=_now_iso())
            read += len(chunk)
            if b"OS48-FENCE" in chunk or b"OS48-FENCE" in self._tail_window + chunk:
                found = _boundary()
                if found is not None:
                    return self._publish_fence(found)
            self._tail_window = (self._tail_window + chunk)[-128:]

    _tail_window: bytes = b""

    def _fence_path(self) -> bytes:
        return capture_mod.capture_fence_path(self.capture.path, self.incarnation)

    # ---- OS-48 F-001: the AUTHORITATIVE interval and the frozen sidecar -----------------
    def _authoritative_interval(self) -> tuple[int, int] | None:
        """``(baseline, N)`` once a verified fence is bound; ``None`` before finality."""
        if self._boundary is None:
            return None
        offset_n = int(self._boundary["offset_n"])
        return min(max(0, int(self._settlement_baseline or 0)), offset_n), offset_n

    def _authoritative_text(self) -> str:
        """The transcript reading of ``[baseline, N)`` -- the ONLY bytes a settlement may
        parse, extract a body from, fall back to, or digest (REVIEW_IMPLEMENTATION F-001: a
        later writer's bytes after N are a diagnostic tail and can change nothing).  Before a
        fence exists there is no authoritative interval and this is the empty string."""
        interval = self._authoritative_interval()
        if interval is None:
            return ""
        return capture_mod.BoundedCapture.transcript_of(self.capture.raw(interval[0])[:interval[1] - interval[0]])

    def _sidecar_at_boundary(self) -> dict[str, Any]:
        """REVIEW_IMPLEMENTATION_iteration2 F-001: the declared sidecar as the WATCHER snapshotted
        it AT the boundary (read + digested in the reap step, before the marker).  Never a
        live read of the path -- a digest first sampled after N proves nothing about [0,N).
        ``{"state": present|absent|sidecar_unproven|none_declared, "record", "raw"}``."""
        if not self.last_message_path:
            return {"state": capture_mod.SIDECAR_STATE_NONE, "record": None, "raw": None}
        return capture_mod.read_sidecar_snapshot(self.capture.path, self.incarnation, fence=self.fence)

    def _sidecar_fence_field(self) -> dict[str, Any] | None:
        snap = self._sidecar_at_boundary()
        if snap["state"] == capture_mod.SIDECAR_STATE_NONE:
            return None
        if snap["record"] is None:
            return {"state": capture_mod.SIDECAR_STATE_UNPROVEN}
        return {k: snap["record"].get(k) for k in ("state", "path", "sha256", "bytes", "instant", "error")}

    def _freeze_sidecar(self, fence_record: Mapping[str, Any]) -> str | None:
        """Bind R3's sidecar PRESENCE fact to the fence: the state the owner recorded at its
        reap-step read (`present` / `absent` / `sidecar_unreadable` / `sidecar_unproven`),
        immutable once published -- late creation, deletion or resize of the file can never
        change the verdict (REVIEW_IMPLEMENTATION_iteration3 F-001 (a)).  Sidecar CONTENT is
        never a settlement body source (F-001 (b): the design has one boundary, the marker's
        stream offset; file content cannot be bound to it), so nothing is frozen for
        extraction.  Returns ``None``; the state is exposed as ``_sidecar_state``."""
        self._sidecar_frozen = None
        recorded = fence_record.get("sidecar") if isinstance(fence_record.get("sidecar"), dict) else None
        if not self.last_message_path and recorded is None:
            self._sidecar_state = capture_mod.SIDECAR_STATE_NONE
            return None
        if recorded is None:
            self._sidecar_state = capture_mod.SIDECAR_STATE_UNPROVEN
            return None
        state = str(recorded.get("state") or capture_mod.SIDECAR_STATE_UNPROVEN)
        self._sidecar_state = state if state in (capture_mod.SIDECAR_STATE_PRESENT, capture_mod.SIDECAR_STATE_ABSENT,
                                                 capture_mod.SIDECAR_STATE_UNREADABLE) else capture_mod.SIDECAR_STATE_UNPROVEN
        return None
    def _release_path(self) -> bytes:
        return capture_mod.release_record_path(self.capture.path, self.incarnation)

    def _owner_dir(self) -> str:
        return os.path.dirname(os.fspath(self.capture.path))

    def _self_identity(self, role: str) -> dict[str, Any]:
        return identity.process_identity(
            pid=os.getpid(), start_id=pty_supervisor.proc_start_ticks(os.getpid()),
            boot_id=pty_supervisor.host_boot_id(), incarnation=self.fence,
            source=pty_supervisor.evidence_source_id())

    def _emitter_identity(self) -> dict[str, Any]:
        record = self.record or {}
        return identity.process_identity(
            pid=int(record.get("pid") or 0), start_id=int(record.get("proc_start_ticks") or 0),
            boot_id=str(record.get("boot_id") or ""), incarnation=self.fence,
            source=str(record.get("evidence_source") or pty_supervisor.evidence_source_id()))

    def _publish_fence(self, drained: dict[str, Any]) -> dict[str, Any]:
        """The SUPERVISING session's fence publication (DESIGN §1.5 / §2.5).  On ``ended ==
        "marker"``: claim generation g1 (tmp+fsync+link -- exclusive; a lost claim is
        `owner_conflict`), then publish the fence (link-exclusive; a lost link means someone
        already published -- read and VERIFY, never overwrite).  Every other end is a NAMED
        non-success and publishes nothing.  ``finality`` is set to
        :data:`FINALITY_CAPTURE_FINALIZED` ONLY when a verified fence exists."""
        ended = drained.get("ended")
        drained["finality"] = "none"
        if ended != "marker":
            drained["finality_detail"] = {
                "budget": "the fence marker was not observed within the post-exit drain bound",
                "master_unreadable": f"the master could not be read to the marker ({drained.get('errno')})",
                "marker_inconsistent": "the fence marker occurs more than once in the capture",
            }.get(str(ended), "the fence marker was not observed")
            drained["outcome"] = capture_mod.OUTCOME_BOUNDARY_UNPROVEN
            return drained
        sentinel = self._read_sentinel()
        if sentinel["outcome"] == "exited":
            exit_how, exit_code = "exit_sentinel", sentinel["code"]
        elif self.exit_proof is not None and self.exit_proof.get("proven"):
            exit_how, exit_code = str(self.exit_proof.get("how") or "ladder"), None
        else:
            drained["finality_detail"] = "the exit is not proven; nothing binds the boundary"
            drained["outcome"] = "exit_unproven"
            return drained
        offset_n, marker_len = int(drained["offset_n"]), int(drained["marker_len"])
        fence_path = self._fence_path()
        existing = capture_mod.read_capture_fence(fence_path, fence=self.fence)
        if existing["outcome"] != capture_mod.EVIDENCE_FINAL:
            owner = self._self_identity(capture_mod.OWNER_SUPERVISOR)
            emitter = self._emitter_identity()
            leader_pid = int((self.pty or {}).get("leader_pid") or 0)
            reaper = identity.process_identity(
                pid=leader_pid, start_id=int((self.pty or {}).get("watcher_start_id") or 0),
                boot_id=str((self.pty or {}).get("boot_id") or pty_supervisor.host_boot_id()),
                incarnation=self.fence, source=pty_supervisor.evidence_source_id())
            # REVIEW_IMPLEMENTATION F-005: NO claim and NO publication without positive,
            # complete identities for the owner (this process), the emitter (the pinned
            # root) and the reaper (the watcher).  An unreadable start identity is the
            # named LOST outcome `identity_unreadable`, never a zero inside a proof.
            for axis, candidate in (("owner", owner), ("emitter", emitter), ("reaped_by", reaper)):
                if not identity.identity_complete(candidate):
                    self._journal(kind="EVENT", derived_from="pty", event="identity_unreadable",
                                  state=self.state, vocabulary={"axis": axis, "identity": dict(candidate)})
                    drained["finality_detail"] = f"the {axis} identity is not positively readable"
                    drained["outcome"] = identity.IDENTITY_UNREADABLE
                    return drained
            evidence = {"predecessor_generation": 0, "predecessor": {}, "relinquish_record": False,
                        "death_witness": capture_mod.EVIDENCE_FINAL, "highest_owner_alive": None}
            generation = capture_mod.make_owner_generation(
                fence=self.fence, generation=1, owner_role=capture_mod.OWNER_SUPERVISOR,
                owner=owner, claim_reason="supervisor_alive_at_exit", superseded=None,
                death_evidence="", claimed_at=_now_iso())
            refused = capture_mod.claim_generation(self._owner_dir(), self.incarnation,
                                                   generation, evidence)
            if refused is not None:
                highest, highest_rec, _state = capture_mod.read_generations(self._owner_dir(), self.incarnation)
                self._journal(kind="EVENT", derived_from="pty", event="owner_conflict",
                              state=self.state, vocabulary={"refusal": refused,
                                                            "highest_generation": highest,
                                                            "highest_owner": (highest_rec or {}).get("owner")})
                drained["finality_detail"] = f"the finalizer generation could not be claimed ({refused})"
                drained["outcome"] = refused
                return drained
            self._journal(kind="EVENT", derived_from="pty", event="owner_claimed",
                          state=self.state, vocabulary={"generation": 1, "owner": owner})
            data = self.capture.raw()
            # PR #36 finding 1: the capture's answerability is MEASURED here, at publish, and
            # bound into the fence as the fact a fenced settlement reads; the provenance
            # entry is listed only when the measurement is positive.
            publish_state = self._capture_state_at_publish()
            record = capture_mod.make_capture_fence(
                fence=self.fence, emitter=emitter,
                emitter_pgid=int((self.record or {}).get("pgid") or 0),
                offset_n=offset_n, marker_len=marker_len, marker_nonce=self.fence_nonce,
                sha256_prefix=capture_mod.prefix_digest(data, offset_n),
                tail_bytes_at_publish=max(0, len(data) - offset_n - marker_len),
                exit_how=exit_how, exit_code=exit_code,
                reaped_by=reaper,
                owner=generation, evidence_source=pty_supervisor.evidence_source_id(),
                provenance=[pty_supervisor.capture_mod_PROVENANCE_REAPED,
                            pty_supervisor.capture_mod_PROVENANCE_MARKER_WRITTEN,
                            pty_supervisor.capture_mod_PROVENANCE_MARKER_OBSERVED]
                           + ([pty_supervisor.capture_mod_PROVENANCE_ANSWERABLE_AT_PUBLISH]
                              if publish_state["answerable"] else [])
                           + [pty_supervisor.capture_mod_PROVENANCE_OWNER_CLAIMED,
                              pty_supervisor.capture_mod_PROVENANCE_SENTINEL if exit_how == "exit_sentinel" else exit_how],
                published_at=_now_iso(),
                sidecar=self._sidecar_fence_field(), capture_state=publish_state)
            try:
                capture_mod.write_capture_fence(fence_path, record)
            except OSError as exc:
                drained["finality_detail"] = f"fence not written: {exc}"
                drained["outcome"] = capture_mod.OUTCOME_FENCE_MISSING
                return drained
            existing = capture_mod.read_capture_fence(fence_path, fence=self.fence)
        return self._bind_fence(drained, existing, sentinel)

    def _capture_state_at_publish(self) -> dict[str, Any]:
        """PR #36 finding 1: the store's answerability and counters AT PUBLISH (an adopted /
        masterless publisher re-reads the meta first), in the fence's closed shape."""
        if self.adopted:
            self.capture.refresh()
        return capture_mod.capture_state_at_publish(
            answerable=self.capture.completion_is_answerable(),
            truncation=self.capture.truncation, dropped_bytes=self.capture.dropped_bytes,
            total_bytes=self.capture.size)

    def _bind_fence(self, drained: dict[str, Any], existing: Mapping[str, Any],
                    sentinel: Mapping[str, Any]) -> dict[str, Any]:
        """Verify a fence on disk against the capture and the sentinel; only a MATCH is final."""
        if existing["outcome"] != capture_mod.EVIDENCE_FINAL:
            drained["finality"] = str(existing["outcome"])
            drained["finality_detail"] = str(existing.get("detail") or "")
            drained["outcome"] = (capture_mod.OUTCOME_LEGACY_FINALIZED
                                  if existing["outcome"] == capture_mod.OUTCOME_LEGACY_FINALIZED
                                  else capture_mod.OUTCOME_FENCE_FOREIGN if existing["outcome"] == "foreign"
                                  else capture_mod.OUTCOME_FENCE_MISSING)
            return drained
        record = existing["record"]
        bound = capture_mod.fence_matches(record, capture=self.capture.path,
                                          sentinel_code=sentinel.get("code"),
                                          sentinel_present=sentinel["outcome"] == "exited")
        if not bound["matches"]:
            drained["finality"] = "mismatch"
            drained["finality_detail"] = str(bound["reason"]) + (f":{bound['axis']}" if bound.get("axis") else "")
            drained["outcome"] = (identity.IDENTITY_UNREADABLE if bound["reason"] == "identity_unreadable"
                                  else capture_mod.OUTCOME_FENCE_MISMATCH)
            return drained
        boundary = record["boundary"]
        sidecar_refusal = self._freeze_sidecar(record)
        if sidecar_refusal is not None:
            drained["finality"] = "mismatch"
            drained["finality_detail"] = "the -o sidecar no longer matches the digest the fence froze"
            drained["outcome"] = sidecar_refusal
            return drained
        self._boundary = {"offset_n": int(boundary["offset_n"]),
                          "marker_len": int(boundary.get("marker_len") or 0),
                          "fence": record}
        drained["finality"] = FINALITY_CAPTURE_FINALIZED
        drained["offset_n"] = int(boundary["offset_n"])
        drained["marker_len"] = int(boundary.get("marker_len") or 0)
        drained["fence"] = {k: record.get(k) for k in ("owner", "exit", "evidence_source", "published_at")}
        drained["fence"]["boundary"] = dict(boundary)
        self._journal(kind="EVENT", derived_from="pty", event="fence_published",
                      state=self.state,
                      vocabulary={"offset_n": int(boundary["offset_n"]),
                                  "sha256_prefix": boundary.get("sha256_prefix"),
                                  "owner": record.get("owner"), "exit": record.get("exit"),
                                  "provenance": record.get("provenance"),
                                  # PR #36 finding 1: the answerability fact the settlement reads
                                  "capture_at_publish": record.get("capture_at_publish")})
        return drained

    def _fence_from_disk(self, drained: dict[str, Any]) -> dict[str, Any]:
        """A MASTERLESS session (adopted; the supervisor that held the master is gone):
        finality is the fence on disk, verified against the capture and the sentinel.  Without
        a fence, a SUCCESSOR may publish one ONLY when the marker is already in the capture,
        the exit is proven, and the highest owner generation is absent, relinquished or
        positively dead (a per-pid identity read, never a scan) -- DESIGN §1.6 C2/C7."""
        sentinel = self._read_sentinel()
        drained["sentinel"] = sentinel["outcome"]
        existing = capture_mod.read_capture_fence(self._fence_path(), fence=self.fence,
                                                  legacy_path=capture_mod.capture_finalized_path(
                                                      self.capture.path, self.incarnation))
        if existing["outcome"] == capture_mod.EVIDENCE_FINAL:
            return self._bind_adopted(drained, existing, sentinel)
        if existing["outcome"] != "absent":
            return self._bind_adopted(drained, existing, sentinel)
        # No fence.  Can THIS successor publish one?  The orphan watcher writes the sentinel
        # BEFORE it drains the marker into the capture file and publishes, so a successor
        # that reads the sentinel first waits -- fence-first, then the capture -- bounded by
        # the drain budget; silence at the bound is `boundary_unproven`, named.
        nonce = str(self.fence_nonce or "")
        deadline = self._clock() + self.profile.timeouts.post_exit_drain_budget_ms / 1000.0
        while True:
            data = self.capture.raw()
            offset_n, marker_len, state = capture_mod.marker_span(data, nonce) if nonce else (-1, 0, capture_mod.EVIDENCE_UNKNOWN)
            if state in (capture_mod.EVIDENCE_FINAL, capture_mod.EVIDENCE_INCONSISTENT):
                break
            if self._clock() >= deadline:
                break
            time.sleep(0.05)
            existing = capture_mod.read_capture_fence(self._fence_path(), fence=self.fence)
            if existing["outcome"] != "absent":
                return self._bind_adopted(drained, existing, sentinel)
        if state != capture_mod.EVIDENCE_FINAL:
            drained["finality"] = "none"
            drained["finality_detail"] = ("the fence marker is not in the capture; the boundary "
                                          "cannot be proven by a successor")
            drained["outcome"] = capture_mod.OUTCOME_BOUNDARY_UNPROVEN
            return drained
        if sentinel["outcome"] == "exited":
            exit_how, exit_code = "exit_sentinel", sentinel["code"]
        elif self.exit_proof is not None and self.exit_proof.get("proven"):
            exit_how, exit_code = str(self.exit_proof.get("how") or "table"), None
        else:
            drained["finality"] = "none"
            drained["outcome"] = "exit_unproven"
            drained["finality_detail"] = "no sentinel and no proven exit of the pinned incarnation"
            return drained
        # A lost claim race is not a verdict: the winner (the orphan watcher, or another
        # successor) is publishing the fence NOW.  Re-evaluate fence-first, bounded by the drain
        # budget: a fence that appears is verified and bound; a live winner without a fence is
        # `finalizer_alive` at the bound; a winner that died without publishing is superseded
        # by a fresh claim (its death is read per pid, never scanned).
        deadline = self._clock() + self.profile.timeouts.post_exit_drain_budget_ms / 1000.0
        while True:
            outcome = self._successor_attempt(drained, sentinel, data, offset_n, marker_len,
                                              nonce, exit_how, exit_code)
            if outcome is None:
                return drained
            if outcome not in (capture_mod.OUTCOME_OWNER_CONFLICT, capture_mod.OUTCOME_FINALIZER_ALIVE):
                return drained
            if self._clock() >= deadline:
                return drained
            time.sleep(0.05)
            existing = capture_mod.read_capture_fence(self._fence_path(), fence=self.fence)
            if existing["outcome"] != "absent":
                return self._bind_adopted(drained, existing, sentinel)
            data = self.capture.raw()

    def _successor_attempt(self, drained: dict[str, Any], sentinel: Mapping[str, Any],
                           data: bytes, offset_n: int, marker_len: int, nonce: str,
                           exit_how: str, exit_code: int | None) -> str | None:
        """One successor claim-and-publish attempt (see :meth:`_fence_from_disk`).  ``None``
        when ``drained`` is final (bound); else the NAMED refusal (also set on ``drained``)."""
        highest, highest_rec, gstate = capture_mod.read_generations(self._owner_dir(), self.incarnation)
        if gstate != capture_mod.EVIDENCE_FINAL:
            drained["finality"] = "none"
            drained["outcome"] = "identity_unreadable"
            drained["finality_detail"] = "the owner generations could not be read"
            return "identity_unreadable"
        alive: bool | None = None
        witness = capture_mod.EVIDENCE_FINAL
        relinquish = False
        predecessor = None
        if highest and highest_rec:
            predecessor = highest_rec.get("owner") or {}
            observed = self._identity_reader(int(predecessor.get("pid") or 0))
            if observed["start_state"] == "absent":
                alive, witness = False, capture_mod.EVIDENCE_FINAL      # ESRCH: that incarnation is gone
            elif observed["start_state"] != capture_mod.EVIDENCE_FINAL:
                alive, witness = None, capture_mod.EVIDENCE_UNREADABLE  # EPERM/other: no evidence
            else:
                alive = int(observed["start_id"]) == int(predecessor.get("start_id") or -1)
                witness = capture_mod.EVIDENCE_UNKNOWN if alive else capture_mod.EVIDENCE_FINAL
            relinquish = capture_mod.read_relinquish(self._owner_dir(), self.incarnation, highest)["outcome"] == "present"
        action, outcome = capture_mod.may_claim_generation(
            fence_published=False, highest_owner_alive=alive, relinquish_record=relinquish,
            death_witness=witness)
        if action != "claim":
            drained["finality"] = "none"
            drained["outcome"] = outcome or capture_mod.OUTCOME_FINALIZER_ALIVE
            drained["finality_detail"] = f"succession refused: {drained['outcome']}"
            return str(drained["outcome"])
        owner = self._self_identity(capture_mod.OWNER_SUCCESSOR)
        emitter = self._emitter_identity()
        for axis, candidate in (("owner", owner), ("emitter", emitter)):
            if not identity.identity_complete(candidate):        # F-005
                drained["finality"] = "none"
                drained["outcome"] = identity.IDENTITY_UNREADABLE
                drained["finality_detail"] = f"the {axis} identity is not positively readable"
                return identity.IDENTITY_UNREADABLE
        evidence = {"predecessor_generation": highest, "predecessor": predecessor or {},
                    "relinquish_record": relinquish, "death_witness": witness,
                    "highest_owner_alive": alive}
        generation = capture_mod.make_owner_generation(
            fence=self.fence, generation=highest + 1, owner_role=capture_mod.OWNER_SUCCESSOR,
            owner=owner, claim_reason="successor_owner_dead" if highest else "successor_no_owner",
            superseded=predecessor, death_evidence=("relinquish_record" if relinquish else
                                                    ("esrch_or_start_identity_mismatch" if highest else "")),
            claimed_at=_now_iso())
        refused = capture_mod.claim_generation(self._owner_dir(), self.incarnation, generation, evidence)
        if refused is not None:
            drained["finality"] = "none"
            drained["outcome"] = refused
            drained["finality_detail"] = f"succession lost: {refused}"
            return refused
        publish_state = self._capture_state_at_publish()          # PR #36 finding 1
        record = capture_mod.make_capture_fence(
            fence=self.fence, emitter=emitter,
            emitter_pgid=int((self.record or {}).get("pgid") or 0),
            offset_n=offset_n, marker_len=marker_len, marker_nonce=nonce,
            sha256_prefix=capture_mod.prefix_digest(data, offset_n),
            tail_bytes_at_publish=max(0, len(data) - offset_n - marker_len),
            exit_how=exit_how, exit_code=exit_code, reaped_by=None, owner=generation,
            evidence_source=pty_supervisor.evidence_source_id(),
            provenance=[pty_supervisor.capture_mod_PROVENANCE_MARKER_OBSERVED,
                        pty_supervisor.capture_mod_PROVENANCE_OWNER_CLAIMED,
                        "published_by_successor_from_captured_marker"]
                       + ([pty_supervisor.capture_mod_PROVENANCE_ANSWERABLE_AT_PUBLISH]
                          if publish_state["answerable"] else []),
            published_at=_now_iso(), sidecar=self._sidecar_fence_field(),
            capture_state=publish_state)
        try:
            capture_mod.write_capture_fence(self._fence_path(), record)
        except OSError as exc:
            drained["finality"] = "none"
            drained["outcome"] = capture_mod.OUTCOME_FENCE_MISSING
            drained["finality_detail"] = f"fence not written: {exc}"
            return capture_mod.OUTCOME_FENCE_MISSING
        self._bind_adopted(drained, capture_mod.read_capture_fence(self._fence_path(), fence=self.fence), sentinel)
        return None

    def _bind_adopted(self, drained: dict[str, Any], existing: Mapping[str, Any],
                      sentinel: Mapping[str, Any]) -> dict[str, Any]:
        """Masterless binding: verify the fence, then (F-003) reconcile the release boundary
        from the record or the captured RELEASE marker so the settlement names the tail."""
        out = self._bind_fence(drained, existing, sentinel)
        if out.get("finality") == FINALITY_CAPTURE_FINALIZED:
            out["release"] = self._recover_release_boundary()
        return out

    def _recover_release_boundary(self) -> dict[str, Any]:
        """REVIEW_IMPLEMENTATION F-003, the ADOPTED (masterless) side of the release protocol:
        verify the `release.<inc>` record against the fence and the capture, or recover the
        boundary R from a single RELEASE marker already in the capture (the custodian died
        between release-1 and its record) and publish the record as `successor_from_capture`;
        a duplicated marker is `inconsistent` -> `diagnostic_tail_unaccounted`; nothing at all
        is `release_record_missing`.  `[0, N)` and the fence are never touched."""
        out: dict[str, Any] = {"offset_r": -1, "retained_tail_bytes": 0, "post_release_bytes": None,
                               "state": capture_mod.EVIDENCE_UNKNOWN, "outcome": None,
                               "recovered_by": "adoption"}
        if self._boundary is None:
            out["outcome"] = capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED
            return out
        data = self.capture.raw()
        fence_record = self._boundary["fence"]
        offset_n = int(self._boundary["offset_n"])
        marker_len = int(self._boundary.get("marker_len") or 0)
        nonce = str(fence_record.get("boundary", {}).get("marker_nonce") or self.fence_nonce or "")
        on_disk = capture_mod.read_release_record(self._release_path(), fence=self.fence)
        if on_disk["outcome"] == capture_mod.EVIDENCE_FINAL:
            joined = capture_mod.verify_release_record(on_disk["record"], capture=data,
                                                       fence_path=self._fence_path(),
                                                       fence_record=fence_record)
            record = on_disk["record"]
            if joined["matches"]:
                out.update({"offset_r": int(record["offset_r"]),
                            "retained_tail_bytes": int(record["retained_tail_bytes"]),
                            "retained_tail_sha256": record["retained_tail_sha256"],
                            "state": capture_mod.EVIDENCE_FINAL, "recovered_by": "record_verified"})
            else:
                out.update({"state": capture_mod.EVIDENCE_INCONSISTENT,
                            "outcome": capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED,
                            "release_record": joined["reason"]})
            self._release = out
            return out
        offset_r, _r_len, state, outcome = capture_mod.recover_release_boundary(
            data, nonce, offset_n, release_record_present=on_disk["outcome"] not in ("absent",))
        if state != capture_mod.EVIDENCE_FINAL:
            out.update({"state": state, "outcome": outcome,
                        "release_record": capture_mod.OUTCOME_RELEASE_RECORD_MISSING
                        if on_disk["outcome"] == "absent" else on_disk["outcome"]})
            self._release = out
            return out
        tail = data[offset_n + marker_len:offset_r]
        record = capture_mod.make_release_record(
            fence=self.fence, fence_file_sha256=capture_mod.file_digest(self._fence_path()),
            release_nonce=nonce, offset_r=offset_r, retained_tail_bytes=len(tail),
            retained_tail_sha256=capture_mod.prefix_digest(tail, len(tail)),
            custodian=self._self_identity(capture_mod.OWNER_SUCCESSOR),
            custodian_role=capture_mod.OWNER_SUCCESSOR, state=capture_mod.EVIDENCE_FINAL,
            published_at=_now_iso())
        try:
            capture_mod.write_release_record(self._release_path(), record)
        except OSError:
            pass
        verified = capture_mod.read_release_record(self._release_path(), fence=self.fence)
        if (verified["outcome"] == capture_mod.EVIDENCE_FINAL
                and capture_mod.verify_release_record(verified["record"], capture=data,
                                                      fence_path=self._fence_path(),
                                                      fence_record=fence_record)["matches"]):
            out.update({"offset_r": offset_r, "retained_tail_bytes": len(tail),
                        "retained_tail_sha256": record["retained_tail_sha256"],
                        "state": capture_mod.EVIDENCE_FINAL, "recovered_by": "successor_from_capture"})
        else:
            out.update({"offset_r": offset_r, "retained_tail_bytes": len(tail),
                        "state": capture_mod.EVIDENCE_UNKNOWN,
                        "outcome": capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED,
                        "release_record": capture_mod.OUTCOME_RELEASE_RECORD_MISSING})
        self._release = out
        self._journal(kind="EVENT", derived_from="pty", event="release_observed",
                      state=self.state, vocabulary=dict(out))
        return out

    def _release_two_phase(self) -> dict[str, Any]:
        """OS-48 DESIGN §1.8, supervisor side: release-1 (the watcher writes the RELEASE marker),
        drain the master to R, publish `release.<inc>` (exclusive), release-2 (the watcher
        closes its slave reference), read to EOF/EIO and journal the post-release count.
        Every non-success is NAMED (`diagnostic_tail_unaccounted`, `release_record_missing`);
        the authoritative prefix is never touched."""
        out: dict[str, Any] = {"offset_r": -1, "retained_tail_bytes": 0, "post_release_bytes": 0,
                               "state": capture_mod.EVIDENCE_UNKNOWN, "outcome": None}
        if self.pty is None or int(self.pty.get("master_fd", -1)) < 0 or self._boundary is None:
            out["outcome"] = capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED
            return out
        fd = int(self.pty["master_fd"])
        nonce = str(self.fence_nonce or "")
        offset_n = int(self._boundary["offset_n"])
        marker_len = int(self._boundary.get("marker_len") or 0)
        if not pty_supervisor.request_release_1(self.pty):
            # C3: no watcher can serve release-1 (it is gone): the tail boundary is unknown
            # and no record can exist -- both named.
            out["outcome"] = capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED
            out["release_record"] = capture_mod.OUTCOME_RELEASE_RECORD_MISSING
            self._release = out
            self._journal(kind="EVENT", derived_from="pty", event="release_observed",
                          state=self.state, vocabulary=dict(out))
            return out
        deadline = self._clock() + self.profile.timeouts.post_exit_drain_budget_ms / 1000.0
        offset_r, r_len = -1, 0
        while self._clock() < deadline:
            data = self.capture.raw()
            offset_r, r_len, state = capture_mod.find_release_marker(data, nonce, after=offset_n)
            if state == capture_mod.EVIDENCE_FINAL:
                break
            if state == capture_mod.EVIDENCE_INCONSISTENT:
                offset_r = -1
                break
            try:
                ready, _, _ = select.select([fd], [], [], 0.05)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            try:
                chunk = self._master_reader(fd, self.profile.capture.read_chunk)
            except OSError:
                break
            if not chunk:
                break
            self.capture.append(chunk, at=_now_iso())
        if offset_r >= 0:
            data = self.capture.raw()
            tail = data[offset_n + marker_len:offset_r]
            record = capture_mod.make_release_record(
                fence=self.fence, fence_file_sha256=capture_mod.file_digest(self._fence_path()),
                release_nonce=nonce, offset_r=offset_r, retained_tail_bytes=len(tail),
                retained_tail_sha256=capture_mod.prefix_digest(tail, len(tail)),
                custodian=self._self_identity(capture_mod.OWNER_SUPERVISOR),
                custodian_role=capture_mod.OWNER_SUPERVISOR, state=capture_mod.EVIDENCE_FINAL,
                published_at=_now_iso())
            out.update({"offset_r": offset_r, "retained_tail_bytes": len(tail),
                        "retained_tail_sha256": record["retained_tail_sha256"]})
            try:
                published = capture_mod.write_release_record(self._release_path(), record)
            except OSError as exc:
                published = False
                out["publish_error"] = f"{type(exc).__name__}:{getattr(exc, 'errno', '')}"
            on_disk = capture_mod.read_release_record(self._release_path(), fence=self.fence)
            if on_disk["outcome"] == capture_mod.EVIDENCE_FINAL:
                # F-003: `final` means the record is ON DISK and joins this proof -- a lost
                # link race to an equivalent record is verified, never assumed.
                joined = capture_mod.verify_release_record(
                    on_disk["record"], capture=data, fence_path=self._fence_path(),
                    fence_record=self._boundary["fence"])
                if joined["matches"]:
                    out["state"] = capture_mod.EVIDENCE_FINAL
                    out["published_by_this_process"] = bool(published)
                else:
                    out["state"] = capture_mod.EVIDENCE_INCONSISTENT
                    out["outcome"] = capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED
                    out["release_record"] = joined["reason"]
            else:
                # the record could not be published (ENOSPC, EROFS, ...): the tail boundary
                # is observed in the stream but NOT durable -- named, never `final`.
                out["state"] = capture_mod.EVIDENCE_UNKNOWN
                out["outcome"] = capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED
                out["release_record"] = capture_mod.OUTCOME_RELEASE_RECORD_MISSING
        else:
            out["outcome"] = capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED
            out["release_record"] = capture_mod.OUTCOME_RELEASE_RECORD_MISSING
        pty_supervisor._signal_drain_handoff(self.pty)          # release-2
        post = 0
        eof_deadline = self._clock() + 2.0
        while self._clock() < eof_deadline:
            try:
                ready, _, _ = select.select([fd], [], [], 0.05)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            try:
                chunk = self._master_reader(fd, self.profile.capture.read_chunk)
            except OSError:
                break
            if not chunk:
                break
            self.capture.append(chunk, at=_now_iso())
            post += len(chunk)
        out["post_release_bytes"] = post
        self._release = out
        self._journal(kind="EVENT", derived_from="pty", event="release_observed",
                      state=self.state, vocabulary=dict(out))
        return out

    def _holders_diagnostic(self) -> dict[str, Any]:
        """DIAGNOSTIC ONLY (DESIGN §2.6): the tty-scoped table rows other than the agent and the
        watcher, for the journal.  Never consulted by any decision; the libproc enumeration is
        not called from the runtime at all."""
        tty = str((self.record or {}).get("captured_tty") or "")
        agent = int((self.record or {}).get("pid") or 0)
        leader = int((self.pty or {}).get("leader_pid") or (self.record or {}).get("sid") or 0)
        rows: list[dict[str, Any]] = []
        if tty:
            try:
                snapshot = self._snapshot()
                rows = [dict(row) for row in snapshot.get("rows", ())
                        if int(row.get("pid", 0)) not in (agent, leader)]
            except Exception:  # noqa: BLE001
                rows = []
        return {"method": "ps_t_diagnostic", "tty": tty, "rows": rows,
                "note": "diagnostic only; a negative enumeration is never evidence"}

    def _watcher_signal(self, sig: int) -> str:
        """OS-48 DESIGN §2.3: deliver ``sig`` to the agent THROUGH THE WATCHER (its parent) over
        the control socket -- ``sent`` or a named refusal.  Without a control end (an adopted
        session, a raw test session) the target is `signal_unbound`: no integer-pid kill."""
        control = (self.pty or {}).get("control_fd")
        if not isinstance(control, int) or control < 0:
            return "refused:" + identity.SIGNAL_UNBOUND
        return pty_supervisor.request_watcher_signal(control, sig, self.fence)

    def _leader_alive(self) -> bool:
        """A NON-reaping, zombie-aware liveness probe of the exit watcher (this supervisor's own
        child), PROCESS-scoped (``ps -o stat= -p``), used only for diagnostics and by the
        adopted-session exit-evidence wait.  Never ``waitpid``s the leader."""
        leader = int((self.pty or {}).get("leader_pid") or (self.record or {}).get("sid") or 0)
        if leader <= 0:
            return False
        try:
            probe = subprocess.run(["ps", "-o", "stat=", "-p", str(leader)],
                                   capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return False
        if probe.returncode not in (0, 1):
            return False
        stat = (probe.stdout or "").strip()
        if not stat:
            return False
        return not stat.startswith("Z")

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
                chunk = self._master_reader(fd, self.profile.capture.read_chunk)
            except InterruptedError:
                continue
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
            text, minted_session_id=binding_id, liveness=liveness,
            raw=self.capture.raw(),                      # F-002: byte-addressable provenance
            delivery_events=tuple(self.delivery_events))
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
                                          "echo": _journal_echo(evidence.get("echo")),
                                          "refusal_source": evidence.get("refusal_source"),
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
        observed = interrupt_mod.observed_row(snapshot, int(self.record["pid"]),
                                              self._identity_reader)
        try:
            permit = identity.assert_may_act(self.record, "write_input", observed=observed)
        except identity.OwnershipRefused:
            return {"intent_id": self.intent_id, "delivery": "stale_handle",
                    "proof": None, "frame_bytes": 0, "settle_ms": 0}
        identity.require_permit(permit, self.record, "write_input")
        payload = command.get("payload") if isinstance(command, Mapping) else None
        text = payload if isinstance(payload, str) else _canonical(command)
        baseline = self.capture.size
        # OS-48 §1.4: settlement records are selected over [baseline, N) -- the delivery baseline.
        self._settlement_baseline = int(baseline)
        rate = self._measured_ingest_rate
        if rate is None:
            # Measured on a THROWAWAY pty, never on this session's: padding written into a
            # live agent's stdin would be read as input, and the write would block whenever
            # the child is not currently reading.  See `measure_ingest_rate`.
            rate = preflight_mod.measure_ingest_rate()
            self._measured_ingest_rate = rate
        # F-002 / B1: record the DELIVERY EVENT at the moment of the write -- the capture
        # offset just before the bytes go out, the exact payload, and the TRANSPORT the
        # bytes go through: a bracketed-paste frame into a pty whose termios is read NOW
        # (the agent may have changed it since spawn).  This record, not a marker in the
        # output, is what `lifecycle.resolve_delivery_echo` derives the expected echo from.
        delivery_event = {"offset": int(baseline), "payload": text,
                          "transport": pty_supervisor.echo_transport(
                              int(self.pty["master_fd"]), kind="pty_write", framed=True,
                              cols=self.profile.cols),
                          "at": _now_iso()}
        self.delivery_events.append(delivery_event)
        # OS-48 PR #36 finding 4: the baseline and the event's DIGEST-ONLY provenance are
        # DURABLE before the bytes go out (never the prompt: REVIEW_BUGFIX F-001), so a
        # successor adopting this dispatch after a supervisor crash settles over the same
        # [baseline, N) with the same echo provenance (`adopt` restores both).
        self._record_delivery()
        # Round-7 consolidated review, follow-up item 6.  The delivery this write must
        # prove is bound to a DELIVERY INTENT of its own: the identity this dispatch is
        # bound to (the minted id, or the frozen adopted one) and the digest of THIS
        # payload -- the same intent shape `launch_with_prompt` proves against -- so the
        # driver's CONJUNCTIVE delivery selector, not a turn-start record, decides.
        binding_id = self._binding_identity(self.capture.transcript()) or self.session_id
        post_ready_intent = drivers.make_delivery_intent(
            intent_id=self.intent_id, dispatch_id=self.dispatch_id, task_id=self.task_id,
            session_id=binding_id, payload=text,
            argv_digest=str((self.delivery_intent or {}).get("argv_digest") or ""),
            attempt_incarnation=self.incarnation,
            delivery_mode=self.profile.delivery_mode)
        result = drivers.deliver(
            int(self.pty["master_fd"]), text, profile=self.profile,
            measured_ingest_rate=rate,
            verify=lambda working: self._verify_delivery(baseline, working,
                                                         event=delivery_event,
                                                         intent=post_ready_intent))
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
                                  # F-002 / B1: the echo-provenance verdict and the
                                  # transport it was derived from, journalled so an
                                  # `echo_unproven` delivery is visible in the settlement.
                                  "echo": _journal_echo(result.get("echo")),
                                  "refusals": list(result.get("refusals") or ()),
                                  "transport": dict(delivery_event["transport"]),
                                  "pid": self.record["pid"],
                                  "captured_tty": self.record["captured_tty"]})
        return {"intent_id": self.intent_id, **result}

    # -- OS-48 PR #36 finding 4: durable delivery provenance ----------------------------------
    #: the journal row's `event` names (closed): the provenance was recorded; an adoption that
    #: could not restore what the journal names
    DELIVERY_RECORDED_EVENT = "delivery_recorded"
    DELIVERY_UNRESTORED_EVENT = "delivery_provenance_unrestored"
    #: the closed per-event vocabulary of a `delivery_recorded` row -- digests and transport
    #: facts only; there is NO payload key (REVIEW_BUGFIX F-001)
    DELIVERY_EVENT_ROW_KEYS = ("index", "offset", "payload_sha256", "payload_bytes", "transport",
                               "at", "echo_proof")

    def _record_delivery(self) -> None:
        """Persist `_settlement_baseline` + `delivery_events` as ONE journal row
        `delivery_recorded` whose closed vocabulary carries, per event, the offset, the
        `EchoTransport`, the payload's sha256 + byte length, `at`, and the DIGEST-ONLY
        `echo_proof` (`lifecycle.echo_proof`: the echo class by name -- `echo_absent` for an
        `argv` delivery or a pty write with ECHO clear -- or, for an echo-possible pty write,
        the sha256 + length of each echo form the recorded transport can produce).  No byte
        of the prompt is written anywhere durable (REVIEW_BUGFIX i1 F-001: the journal is a
        plain file a stranger reads -- `append_delivery_intent`'s rule -- and an `argv` prompt
        is not in the capture either); the earlier `capture.log.delivery.<inc>.json` record,
        which carried the payload, no longer exists.  A successor restores the baseline and
        payload-less events from this row (`_restore_delivery`) and `resolve_delivery_echo`
        resolves them from the proof to the SAME verdict the live payload produced."""
        self._journal(kind="EVENT", derived_from="driver", event=self.DELIVERY_RECORDED_EVENT,
                      state=self.state,
                      vocabulary={"baseline": int(self._settlement_baseline or 0),
                                  "delivery_mode": self.profile.delivery_mode,
                                  "events": [{"index": i, "offset": int(ev.get("offset", 0) or 0),
                                              "payload_sha256": _sha256_text(str(ev.get("payload") or "")),
                                              "payload_bytes": len(str(ev.get("payload") or "").encode("utf-8")),
                                              "transport": _journal_transport(ev.get("transport")),
                                              "at": str(ev.get("at") or ""),
                                              "echo_proof": lifecycle.echo_proof(
                                                  str(ev.get("payload") or ""),
                                                  ev.get("transport") if isinstance(ev.get("transport"), Mapping) else {})}
                                             for i, ev in enumerate(self.delivery_events)]})

    def _restore_delivery(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """The adopt-side half: from the LAST `delivery_recorded` row of this incarnation,
        restore the baseline and one PAYLOAD-LESS event per recorded event -- offset,
        transport, `at` and the `echo_proof` -- so the fenced selector excludes exactly what
        the live session excluded (an `echo_absent` class by name; a digest-verified span for
        an echo-possible transport) and nothing on any weaker evidence.  A row whose event
        vocabulary is not the closed shape restores NO event and is journalled by name
        (`delivery_provenance_unrestored`): the selector then excludes nothing (fail closed)."""
        self._settlement_baseline = 0
        self.delivery_events = []
        named = [row for row in rows if row.get("kind") == "EVENT"
                 and row.get("event") == self.DELIVERY_RECORDED_EVENT]
        if not named:
            return {"restored": False, "reason": "no_delivery_recorded_row", "events": 0}
        vocab = dict(named[-1].get("source_vocabulary") or {})
        baseline = vocab.get("baseline")
        self._settlement_baseline = baseline if isinstance(baseline, int) and not isinstance(baseline, bool) else 0
        recorded = vocab.get("events")
        reason = ""
        if not isinstance(recorded, list):
            reason = "delivery_recorded_row_malformed"
        elif any(not isinstance(ev, Mapping) or set(ev) != set(self.DELIVERY_EVENT_ROW_KEYS)
                 or "payload" in ev for ev in recorded):
            reason = "delivery_recorded_events_malformed"
        if reason:
            self._journal(kind="EVENT", derived_from="driver", event=self.DELIVERY_UNRESTORED_EVENT,
                          state=self.state,
                          vocabulary={"reason": reason, "baseline": int(self._settlement_baseline),
                                      "events_named": len(recorded) if isinstance(recorded, list) else 0,
                                      "events_restored": 0})
            return {"restored": False, "reason": reason, "events": 0}
        self.delivery_events = [{"offset": int(ev.get("offset", 0) or 0), "payload": "",
                                 "transport": dict(ev["transport"]) if isinstance(ev.get("transport"), Mapping) else None,
                                 "at": str(ev.get("at") or ""),
                                 "echo_proof": dict(ev["echo_proof"]) if isinstance(ev.get("echo_proof"), Mapping) else None}
                                for ev in recorded]
        return {"restored": True, "reason": "", "events": len(self.delivery_events)}

    def _verify_delivery(self, baseline: int, baseline_working: bool, *,
                         event: Mapping[str, Any] | None = None,
                         intent: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Poll for one of the three named proofs, bounded.

        The third proof -- the output sequence advanced -- is admissible ONLY when the agent
        was already working at baseline; otherwise advancing output is just the echo of our
        own frame.

        Round-7 consolidated review, follow-up item 6.  A `turn_start` record ALONE is no
        longer accepted as `delivered_confirmed`: both installed CLIs were MEASURED
        starting a turn on their authentication-failure legs before any prompt could have
        executed, and the driver documentation already said an assistant record alone is
        not delivery proof.  The structured proof for a post-ready write is now the
        driver's OWN conjunctive delivery selector (`driver.delivery_evidence`) applied to
        the bytes after ``baseline`` against the write's delivery ``intent`` -- the same
        class-B proof `launch_with_prompt` requires -- reported as ``agent_response``.  The
        termios echo emulator is NOT extended: `screen_echo` is exactly the proven echo it
        was, and `output_sequence` is unchanged.

        F-002 / B1: the refusal scan is `lifecycle.refusal_evidence` over the RAW capture
        bytes from ``baseline`` -- the same byte offset the delivery ``event`` recorded
        (`capture.size`) -- with the event translated to raw-byte 0 of that slice and its
        recorded TRANSPORT carried whole.  Only a span proven to be the frame's echo under
        that transport is excluded; a refusal that is not the delivered payload -- including
        one identical to task text, whatever ESC / multi-byte bytes precede it -- fires, and
        an unproven echo excludes nothing and is named in the result.  The `screen_echo`
        proof is that same PROVEN echo (`echo_proven`): the frame's bytes at the recorded
        offset under the recorded transport, never the marker string found anywhere.
        """
        deadline = self._clock() + \
            self.profile.timeouts.delivery_verify_timeout_ms / 1000.0
        events = ({**dict(event), "offset": 0},) if event and event.get("payload") else ()
        scan: dict[str, Any] = {"refusals": (), "echo": {"state": "no_delivery",
                                                         "reason": "", "spans": (),
                                                         "events": ()}}
        while self._clock() < deadline:
            self.pump()
            scan = lifecycle.refusal_evidence(
                self.capture.raw(baseline), events,
                structured_refusal=self.driver.structured_refusal)
            if scan["refusals"]:
                return {"delivery": "blocked", "proof": None,
                        "refusals": scan["refusals"], "echo": scan["echo"]}
            text = self.capture.transcript(baseline)
            if intent is not None:
                proof = self.driver.delivery_evidence(
                    text, intent=intent, channel_owned=self.pty is not None,
                    composed_argv=list(self.pty.get("argv", ())) if self.pty else [])
                if proof is not None:
                    self.delivery_proof = dict(proof)
                    return {"delivery": "delivered_confirmed", "proof": "agent_response",
                            "echo": scan["echo"], "delivery_proof": dict(proof)}
            if scan["echo"]["state"] == "echo_proven":
                return {"delivery": "delivered_confirmed", "proof": "screen_echo",
                        "echo": scan["echo"]}
            if baseline_working and self.capture.size > baseline:
                return {"delivery": "delivered_confirmed", "proof": "output_sequence",
                        "echo": scan["echo"]}
        return {"delivery": "not_observed", "proof": None, "echo": scan["echo"]}

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
            identity_reader=self._identity_reader, watcher=self._watcher_signal,
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
        if self.adopted:
            # A stranger's view (finding 1): the exit watcher, not this process, is the
            # one appending to the capture, so the meta and the bytes are re-read every
            # time rather than trusted from memory.
            self.capture.refresh()
        # OS-48 PR #36 finding 1: with a VERIFIED fence the answerability a settlement rests on
        # is the fence's own recorded fact about [0, N) (`capture_at_publish`: the store had
        # lost nothing when the owner published), never the live WHOLE-FILE state -- every
        # limit drop / line cut / failed write / meta disagreement after the publish lies past
        # the bytes the fence bound and is a fact about the diagnostic tail only.  On that
        # path the whole file is not digested or read at all; the store's COUNTERS (the meta's
        # truncation cause, dropped bytes, the irreversible unanswerable cause, the size) are
        # recorded as `post_boundary` -- diagnostic evidence by name, moving nothing.  Before a
        # fence is bound, or for a fence that carries no such fact (published before the field
        # existed), the whole-capture answer stays in force -- fail-closed, as before.
        post_boundary: dict[str, Any] | None = None
        fenced = capture_mod.fenced_answerability(self._boundary.get("fence")) if self._boundary else None
        if fenced is not None and fenced["source"] == capture_mod.ANSWERABILITY_SOURCE_FENCE:
            answerable = fenced
        else:
            answerable = self.capture.completion_is_answerable()
        if self._boundary is not None:
            post_boundary = {"truncation": self.capture.truncation or "",
                             "dropped_bytes": int(self.capture.dropped_bytes),
                             "unanswerable": str(self.capture.unanswerable or ""),
                             "size": int(self.capture.size),
                             "offset_n": int(self._boundary["offset_n"]),
                             "answerability_source": str((fenced or {}).get("source") or "")}
        exit_proven = sentinel["outcome"] == "exited"
        if not exit_proven and self.exit_proof is not None and self.exit_proof["proven"]:
            # The exit was proven by the ownership ladder / the process table with no
            # sentinel to carry the status (a SIGKILLed watcher, DR-2).  The exit is a fact
            # and the status is a NAMED absence -- `None`, never `0`.
            exit_proven = True
        selection = None
        if self._boundary is not None:
            # OS-48 DESIGN §1.4: settlement records are selected over the FENCED range
            # [baseline, N) only -- R1 refusal dominance, R2 exactly one, R3 dispatch binding.
            offset_n = int(self._boundary["offset_n"])
            baseline = min(max(0, int(self._settlement_baseline or 0)), offset_n)
            try:
                # The SEMANTIC rule: only [baseline, N) reaches the selector -- and (PR #36
                # finding 1) the PHYSICAL read is exactly that: ONE bounded read of
                # `N - baseline` bytes at `baseline`.  The marker and whatever diagnostic tail
                # has been appended since are never read, allocated or decoded here, so a
                # tail of any size -- or one too large to allocate -- has no path into this
                # settlement (run_5fcd2beac376 N-003 read the file through EOF and sliced).
                fenced_raw = self.capture.raw(baseline, offset_n - baseline)
                fenced_text = fenced_raw.decode("utf-8", errors="replace")
                # PR #36 finding 2: the selector gets the REAL delivery provenance -- the
                # events `send` / `run_dispatch` recorded (payload, the `EchoTransport` read at
                # the write, the capture offset of the write) -- translated into the fenced
                # range's raw-byte coordinates.  `delivery_intent` carries a digest, never a
                # payload, and could prove no echo; a negative translated offset (a delivery
                # before the baseline) is `delivery_before_window`, unproven, excludes nothing.
                events = tuple({**dict(ev), "offset": int(ev.get("offset", 0) or 0) - baseline}
                               for ev in self.delivery_events if isinstance(ev, Mapping))
                selection = self.driver.select_completion(
                    fenced_text, raw=fenced_raw,
                    bound_value=self._binding_identity(fenced_text) or "",
                    # REVIEW_IMPLEMENTATION_iteration3 F-001 (a): R3's presence fact is the IMMUTABLE
                    # fence snapshot (`present` at the owner's reap-step read), never a live
                    # exists()/getsize() -- a sidecar created, removed or resized after N cannot
                    # change the verdict.  `absent` / unreadable / unproven -> provenance_unbound.
                    sidecar_present=self._sidecar_state == capture_mod.SIDECAR_STATE_PRESENT,
                    delivery_events=events)
                selection = dict(selection)
                selection["settlement_range"] = {
                    "baseline": baseline, "offset_n": offset_n, "read_bytes": len(fenced_raw),
                    "delivery_events": len(events),
                    "echo": str((selection.get("echo") or {}).get("state") or "no_delivery"),
                    "echo_reason": str((selection.get("echo") or {}).get("reason") or "")}
            except MemoryError:
                # REVIEW_IMPLEMENTATION_iteration2 F-017: the reader could not EXAMINE the fenced
                # range for want of memory -- in the parser (already classified inside
                # `parse_json`) or in the reader's own copies / decodes / line splits around
                # it.  Whatever was or was not read, nothing about the range is decided: the
                # NAMED incomplete-scan outcome (`resource_limit`), never an escaping
                # exception and never a settlement.  This bounds the settlement READER only;
                # it is no recovery guarantee for a process-wide allocation failure elsewhere.
                fenced_raw = b""
                selection = {"record": None, "outcome": capture_mod.OUTCOME_RECORD_SCAN_INCOMPLETE,
                             "refusal": None, "candidates": 0,
                             "scan": {"complete": False, "reason": capture_mod.SCAN_INCOMPLETE_RESOURCE,
                                      "examined": 0}}
        # run_7859f202457c F-017 (REVIEW_IMPLEMENTATION_iteration3 of run_5fcd2beac376): with a
        # fence, the SELECTION above is the whole settlement input -- `completion_evidence`
        # takes its record / provenance outcome / refusal from `selection` and never reads
        # `text` when one is supplied.  Iteration 3 nevertheless re-read [baseline, N) here
        # (`_authoritative_text`) and, when THAT second read raised `MemoryError`, replaced
        # an already-selected `refusal_in_boundary` with `record_scan_incomplete` -- a
        # positively established R1 refusal turned into LOST by a read whose result nothing
        # consumed.  The redundant read is gone: once `select_completion` has returned, no
        # further read of the capture takes place in this method, so no later reader
        # failure can reach the selection (DESIGN §1.4 R1: a selected refusal dominates;
        # the same holds for a selected completion record).  Diagnostics that would need
        # the range again must be read AFTER settlement and may never feed back into it.
        # Without a fence there is no selection at all and the legacy last-of-type reading
        # of the transcript is the driver's only input (an allocation failure there leaves
        # `text` empty and `selection` None -- no record, never a settlement).
        text = ""
        if self._boundary is None:
            try:
                text = self.capture.transcript()
            except MemoryError:
                text = ""
        evidence = self.driver.completion_evidence(
            text,
            exit_status=sentinel["code"] if sentinel["outcome"] == "exited" else None,
            exit_proven=exit_proven,
            capture_answerable=answerable["answerable"],
            selection=selection)
        evidence["boundary"] = dict(self._boundary) if self._boundary else None
        if selection is not None and selection.get("settlement_range"):
            evidence["settlement_range"] = dict(selection["settlement_range"])
        if post_boundary is not None:
            evidence["post_boundary"] = post_boundary
        if not answerable["answerable"]:
            # The capture names WHY it cannot answer; the driver only knows THAT it cannot.
            evidence["lost_reason"] = answerable["lost_reason"]
            evidence["capture_integrity"] = answerable.get("integrity", "")
        return evidence

    def release(self) -> None:
        """Drain into the capture, then close the pty master.  The file SURVIVES (AC-37-05).

        Draining into the capture first, so the transcript is complete AND the child can
        finish exiting; `standalone_pty.release` then drains whatever arrived in between and
        closes.
        """
        if self.pty is not None and int(self.pty.get("master_fd", -1)) >= 0:
            self.pump(timeout_ms=100)
            # OS-48 DESIGN §2.5 rule 2: closing the guard while ALIVE is a RELINQUISHMENT, and
            # the watcher may only succeed a live owner through this durable record -- guard EOF
            # alone is never death evidence (probe_d11).
            try:
                highest, _rec, _state = capture_mod.read_generations(self._owner_dir(), self.incarnation)
                if highest and self._boundary is None:
                    capture_mod.write_relinquish(
                        self._owner_dir(), self.incarnation, fence=self.fence, generation=highest,
                        owner=self._self_identity(capture_mod.OWNER_SUPERVISOR),
                        reason="release_handoff", written_at=_now_iso())
            except Exception:  # noqa: BLE001 - the release itself must proceed
                pass
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
        #: Finding 11: one preflight cache per RUN, shared by its sessions.
        self.preflight_cache: dict[str, dict[str, Any]] = {}

    def session_for(self, intent: Mapping[str, Any]) -> StandaloneSession:
        intent_id = str(intent.get("intent_id", ""))
        if intent_id not in self.sessions:
            self.sessions[intent_id] = self._session_factory(
                intent=intent, profile=self.profile, artifact_base=self.artifact_base,
                run_id=self.run_id, journal=self.journal,
                runtime_state=self.runtime_state,
                **({"preflight_cache": self.preflight_cache}
                   if self._session_factory is StandaloneSession else {}),
                **self._session_kwargs)
        return self.sessions[intent_id]

    def session(self, intent_id: str) -> StandaloneSession:
        try:
            return self.sessions[intent_id]
        except KeyError:
            raise KeyError(
                f"no live session for {intent_id!r} in this process; a stranger process "
                "reads the run through standalone_journal.rediscover instead") from None

    #: How long an adopting reader waits for exit evidence that is IN FLIGHT -- the
    #: watcher alive, the agent gone, the sentinel not yet written.  The same figure
    #: `StandaloneAdapter.EXIT_EVIDENCE_BUDGET_MS` uses for `recover_handle`.
    EXIT_EVIDENCE_BUDGET_MS = 2_000

    def adopt_session(self, intent: Mapping[str, Any], *,
                      fence: str) -> tuple[StandaloneSession | None, dict[str, Any]]:
        """Follow-up review finding 1: a session over an effect ANOTHER process created.

        Never reuses a live session of this process for the intent -- a live session
        holds a pty this one must not -- and never registers the adopted one where
        `session()` would hand it to `send`/`interrupt` as if it were supervised here.
        """
        intent_id = str(intent.get("intent_id", ""))
        live = self.sessions.get(intent_id)
        if live is not None and live.record is not None and not live.adopted:
            return None, {"adopted": False,
                          "detail": f"{intent_id!r} is supervised live in this process"}
        session = self._session_factory(
            intent=intent, profile=self.profile, artifact_base=self.artifact_base,
            run_id=self.run_id, journal=self.journal, runtime_state=self.runtime_state,
            **({"preflight_cache": self.preflight_cache}
               if self._session_factory is StandaloneSession else {}),
            **self._session_kwargs)
        outcome = session.adopt(fence=fence)
        if not outcome["adopted"]:
            return None, outcome
        return session, outcome

    def rediscover(self, *, intent_ids: Sequence[str] = ()) -> journal_mod.RunSnapshot:
        """The stranger-process read.  Holds none of a live session's objects."""
        return journal_mod.rediscover(self.run_id, self.artifact_base,
                                      runtime_state=self.runtime_state,
                                      intent_ids=intent_ids)


def _pid_present(pid: int) -> bool:
    """``kill(pid, 0)``: exists (ours or not) vs ESRCH."""
    import errno
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


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


def _readiness_failure_reason(verdict: Mapping[str, Any]) -> str:
    """The NAMED reason a readiness wait failed on.  A `deadline_expired` verdict carries the
    verdict it expired on (`expired_on`), so an `echo_unproven` timeout settles as
    ``deadline_expired(echo_unproven:...)`` rather than as a bare timeout."""
    reason = str(verdict.get("reason", ""))
    expired_on = verdict.get("expired_on")
    if isinstance(expired_on, Mapping) and expired_on.get("reason"):
        return f"{reason}({expired_on['reason']})"
    return reason


def _journal_echo(echo: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The journal-safe form of an `EchoResolution`: state, reason and the per-event
    verdicts (offsets and spans as plain ints) -- never the payload bytes."""
    if not isinstance(echo, Mapping):
        return None
    return {"state": str(echo.get("state", "")), "reason": str(echo.get("reason", "")),
            "spans": [[int(a), int(b)] for a, b in (echo.get("spans") or ())],
            "events": [{"index": int(e.get("index", 0)), "offset": int(e.get("offset", 0)),
                        "state": str(e.get("state", "")), "reason": str(e.get("reason", "")),
                        "span": [int(e["span"][0]), int(e["span"][1])]
                        if e.get("span") else None}
                       for e in (echo.get("events") or ()) if isinstance(e, Mapping)]}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _journal_transport(transport: Any) -> dict[str, Any] | None:
    """The journal-safe form of an `EchoTransport`: kind, framing, columns and the termios
    flags (plain bools / ints already) -- no payload bytes are in it."""
    if not isinstance(transport, Mapping):
        return None
    out = {"kind": str(transport.get("kind", "")), "framed": bool(transport.get("framed")),
           "cols": int(transport.get("cols", 0) or 0), "read_at": str(transport.get("read_at", ""))}
    flags = transport.get("termios")
    out["termios"] = ({str(k): (list(v) if isinstance(v, (list, tuple)) else v) for k, v in flags.items()}
                      if isinstance(flags, Mapping) else None)
    return out


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
