"""OS-43 UR-4: the ENGINE-owned general recovery API for a stalled run.

A sibling of :mod:`pause_runtime`, and owned by the workflow engine for the same reason
that module is: the four things a recovery must do -- decide recoverability, decide the
next node, hold the lease across a blocking re-entry, and refuse to re-run an effect --
are already and exclusively engine responsibilities (``pause_runtime.classify_head``,
``routing.route``, ``lease_keeper.LeaseKeeper``, ``runtime_state.claim`` with
``executor._recover``).  A Watchdog INVOKES this and OBSERVES the answer; it decides
nothing.

The whole contract is expressed in the types rather than in prose a caller has to
remember:

* :class:`RecoveryRequest` is frozen and its field set is closed.  There is no field --
  and no ``**kwargs`` -- through which a caller could express a next node, a Responsible
  Phase, a recoverability verdict, a decision-bundle id, an externally minted lease token
  or a force flag.  A bypass that cannot be expressed cannot be attempted (AC-7).
* :class:`RecoveryOutcome` carries exactly one member of the closed
  :data:`RECOVERY_OUTCOMES` set and a ``code`` from a vocabulary that already exists.  A
  refusal carries NO continuation handle -- no token, no saver, no state, no graph -- so
  there is nothing on it a caller could use to proceed anyway.
* the fencing token is minted by ``claim``, held in this function's frame, and appears on
  no return value.  A caller cannot take a claim and hand it in.

**Which store owns the run-scoped claim is decided by the RUN's durable state, never by
the caller.**  A run with an OS-31 pause record is claimed by that record, and the whole
claimed section is delegated to ``pause_runtime.resume_run`` -- so CON-2 is literal: for a
paused run OS-31 remains the single authority and this module is a translation layer, not
a fork.  A run with no pause record is claimed by the new run-scoped recovery lease
(:mod:`recovery_store`).  A run that has BOTH a pause record and a live recovery lease is
refused with ``CONFLICT``/``RECOVERY_CLAIM_HELD`` before anything is claimed: two
run-scoped leases on one run is exactly the disagreement AC-4 exists to prevent.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import contracts, pause_policy, ports, pause_store, recovery_store
from .lease_keeper import LeaseRenewalFailed, heartbeat_interval_for

# ---- the two recovery kinds ----------------------------------------------------------
RECOVERY_KIND_STALLED_ACTIVE = "stalled_active"
RECOVERY_KIND_PAUSE_CONTINUATION = "pause_continuation"
RECOVERY_KINDS = (RECOVERY_KIND_STALLED_ACTIVE, RECOVERY_KIND_PAUSE_CONTINUATION)

# ---- the closed outcome set ----------------------------------------------------------
RECOVERED = "RECOVERED"
NO_EFFECT = "NO_EFFECT"
NOT_RECOVERABLE = "NOT_RECOVERABLE"
CONFLICT = "CONFLICT"
UNSUPPORTED = "UNSUPPORTED"
REFUSED = "REFUSED"
RECOVERY_OUTCOMES = (RECOVERED, NO_EFFECT, NOT_RECOVERABLE, CONFLICT, UNSUPPORTED,
                     REFUSED)
#: The four a Watchdog may never retry its way past.  ``RECOVERED`` and ``NO_EFFECT`` are
#: the two that say the run is where it should be.
RECOVERY_TERMINAL_OUTCOMES = (NOT_RECOVERABLE, CONFLICT, UNSUPPORTED, REFUSED)

#: Every code any outcome may carry, each from a vocabulary that ALREADY exists.  Kept as
#: a mapping rather than prose so ``recover_stalled_run`` cannot invent one: the returned
#: outcome is asserted against this table before it is handed back.
RECOVERY_OUTCOME_CODES: dict[str, frozenset[str]] = {
    RECOVERED: frozenset({pause_policy.RECOVERY_ADVANCED})
    | pause_policy.PAUSE_RECOVERY_CODES,
    NO_EFFECT: frozenset({pause_policy.RECOVERY_ALREADY_APPLIED,
                          "RESPONSE_ALREADY_APPLIED", "RUN_ALREADY_RESUMED",
                          "RUN_ALREADY_CANCELLED", "RUN_ALREADY_ABANDONED"}),
    NOT_RECOVERABLE: frozenset({
        "STALE_CHECKPOINT_HEAD", "PAUSE_CONTINUATION_UNRECOVERABLE",
        "PAUSE_CHECKPOINT_MISSING", "CHECKPOINT_UNVERIFIED",
        "RECOVERY_HEAD_MISSING", "RECOVERY_NO_RUNNABLE_NODE"}),
    CONFLICT: frozenset({"PAUSE_CLAIM_HELD", "PAUSE_CLAIM_LOST",
                         "PAUSE_OBSERVATION_TIMEOUT", "RECOVERY_CLAIM_HELD",
                         "RECOVERY_CLAIM_LOST", "LEASE_LOST",
                         "IDEMPOTENCY_LEASE_LOST", "IDEMPOTENCY_LEASE_HELD",
                         recovery_store.EXECUTION_AUTHORITY_HELD,
                         "EXECUTION_AUTHORITY_LOST"}),
    UNSUPPORTED: frozenset({"IDEMPOTENCY_RECOVERY_UNSUPPORTED",
                            "LANGGRAPH_DEPENDENCY_MISSING"}),
    REFUSED: frozenset({"PAUSE_RECORD_CORRUPT", "RECOVERY_RECORD_CORRUPT",
                        "PAUSE_LIFECYCLE_INCOHERENT", "SETTLEMENT_JOURNAL_CORRUPT",
                        "RUNTIME_STATE_ERROR", "PAUSE_GENERATION_ACTIVE",
                        "PAUSE_GENERATION_LINEAGE", "PAUSE_PROJECTION_DIVERGED",
                        "RESPONSE_CONFLICT", "RESPONSE_NOT_FOUND",
                        "RESPONSE_STALE_REVISION", "RESPONSE_ITEM_UNRESOLVED",
                        "PAUSE_TRANSITION_FORBIDDEN", "CHECKPOINT_STORE_RETIRED",
                        "PAUSE_NOT_ADMISSIBLE", "PAUSE_RECORD_MISSING",
                        "DISPATCH_UNACCOUNTED", "TERMINAL_OWNERSHIP_UNKNOWN",
                        "TERMINAL_ORPHAN_POSSIBLE", "TERMINAL_IDENTITY_UNVERIFIED"}),
}

# ---- discovery verdicts --------------------------------------------------------------
#: The one ACTIONABLE verdict for a run with no pause record.  A paused run keeps OS-31's
#: own two actionable verdicts, transcribed rather than renamed.
RECOVERY_STALLED_RECOVERABLE = "STALLED_RECOVERABLE"
RUN_ROOT_UNREADABLE = "RUN_ROOT_UNREADABLE"
RECOVERY_RUN_TERMINAL = "RUN_TERMINAL"
DISCOVERY_ACTIONABLE_VERDICTS = (
    frozenset({RECOVERY_STALLED_RECOVERABLE})
    | pause_policy.PAUSE_DISCOVERY_ACTIONABLE_VERDICTS)
DISCOVERY_VERDICTS = (
    DISCOVERY_ACTIONABLE_VERDICTS
    | pause_policy.PAUSE_DISCOVERY_VERDICTS
    | pause_policy.RECOVERY_REFUSAL_CODES
    | frozenset({RUN_ROOT_UNREADABLE, RECOVERY_RUN_TERMINAL, "NO_CHECKPOINT_AUTHORITY"}))

WORKFLOW_CHECKPOINT_FILENAME = ".workflow_checkpoints.json"


class RunsRootUnreadable(OSError):
    """The runs root itself could not be listed.  "Unknown" is never "empty"."""


# ---- the closed request --------------------------------------------------------------
@dataclass(frozen=True)
class RecoveryRequest:
    """Observations of the world, and nothing that decides anything.

    Frozen and closed on purpose (AC-7 mechanism 1).  ``graph_factory`` is runtime
    *wiring*, not a routing decision: ``pause_runtime.resume_run`` already takes one
    (``pause_runtime.py:686``) and ``launcher.run_pause_cli`` builds it from the adapter,
    ledger, approval port and journal without naming a node.
    ``current_repository`` / ``current_artifact`` / ``current_policy_digest`` are
    observations the ENGINE revalidates itself; the caller reports what the tree looks
    like and does not decide what it means.
    """

    run_id: str
    artifact_base: str
    graph_factory: Any
    current_repository: Mapping[str, Any] = field(default_factory=dict)
    current_artifact: Mapping[str, Any] = field(default_factory=dict)
    current_policy_digest: str = ""
    actor_id: str = ""
    actor_type: str = "service"          # closed: "service" | "human"
    #: ``None`` means the store's OWN bounded, lease-derived window
    #: (``pause_store.observe_timeout_for``): never unbounded, never shorter than the
    #: incumbent's lease.
    observe_timeout_seconds: float | None = None
    recursion_limit: int | None = None
    approval_port: Any = None            # OS-31 delegation only; never read on the active branch


@dataclass(frozen=True)
class RecoveryOutcome:
    """One member of the closed outcome set, and the engine's own code for it.

    A refusal (``NOT_RECOVERABLE`` / ``CONFLICT`` / ``UNSUPPORTED`` / ``REFUSED``) carries
    ``effect_performed=False``, no ``resumed_checkpoint_id``, no ``next_pause_record`` and
    no token, saver, state or graph.  There is literally nothing on it with which to
    continue -- AC-7 mechanism 3.
    """

    status: str
    code: str
    recovery_id: str = ""
    recovery_kind: str = ""
    detail: str = ""                     # human text; never parsed by any caller
    resumed_checkpoint_id: str = ""
    effect_performed: bool = False
    head_before: str = ""
    head_after: str = ""
    revalidation_codes: tuple[str, ...] = ()
    next_pause_record: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.status not in RECOVERY_OUTCOMES:
            raise ValueError(f"unknown recovery outcome: {self.status!r}")
        allowed = RECOVERY_OUTCOME_CODES[self.status]
        if self.code not in allowed:
            raise ValueError(
                f"{self.status} may not carry code {self.code!r}; the codes are a closed "
                f"engine vocabulary, not prose -- expected one of {sorted(allowed)}")
        if self.status in RECOVERY_TERMINAL_OUTCOMES and (
                self.effect_performed or self.resumed_checkpoint_id
                or self.next_pause_record is not None):
            raise ValueError(
                f"{self.status} is a refusal and must carry no continuation handle")


def recovery_identity(*, run_id: str, thread_id: str, checkpoint_ns: str,
                      head_checkpoint_id: str, recovery_kind: str) -> str:
    """The idempotency identity, keyed on the COMMITTED head.

    Keying on the head is what makes "same identity" mean "the run has not moved", which
    is exactly when a replay must be a no-op -- ``_recover_continuation`` already uses
    ``after != before`` around ``graph.invoke`` as its SOLE effect evidence
    (``pause_runtime.py:508-517``), and ``FileCheckpointSaver.head`` is written inside the
    same critical section as ``put`` (``checkpoint_store.py:335-345``).  A run that
    genuinely advances therefore yields a DIFFERENT identity and is legitimately
    reconsidered; a run that has not moved is not.
    """
    if recovery_kind not in RECOVERY_KINDS:
        raise ValueError(f"unknown recovery kind: {recovery_kind!r}")
    return contracts.stable_id("recovery", {
        "run_id": run_id, "thread_id": thread_id, "checkpoint_ns": checkpoint_ns,
        "head_checkpoint_id": head_checkpoint_id, "recovery_kind": recovery_kind})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def langgraph_available() -> bool:
    from .launcher import LauncherError, require_runtime
    try:
        require_runtime()
    except LauncherError:
        return False
    return True


def checkpoint_path(run_id: str, *, artifact_base: str | os.PathLike[str]) -> Path:
    """The one checkpoint-store path addressable from a run id alone.

    Deliberately the same default ``turn_boundary.workflow_checkpoint_path`` uses, so the
    Watchdog's observation and the engine's recovery are looking at one store.
    """
    return (Path(artifact_base) / "artifacts" / "runs" / run_id
            / WORKFLOW_CHECKPOINT_FILENAME)


@dataclass(frozen=True)
class _Head:
    """The run's committed head, and what its own routing owes next.  Durable evidence."""

    thread_id: str
    checkpoint_ns: str
    head_checkpoint_id: str
    run_status: str
    next_node: str
    state: Mapping[str, Any]


def resolve_head(run_id: str, *, artifact_base: str | os.PathLike[str],
                 explicit: Any = None) -> _Head | None:
    """The run's single committed head, or ``None`` when the store holds none for it.

    Transcribes ``turn_boundary.read_workflow_checkpoint``'s traversal and
    ``classify_checkpoint_state``'s ``(status, next node)`` rule rather than re-deriving
    either: the Watchdog and the engine must not be able to disagree about what the head
    says.  Threads that disagree about the run's status raise, exactly as that function
    does -- picking one is the inference this boundary refuses to make.
    """
    import json

    from . import routing
    from .checkpoint_store import CheckpointStoreError, FileCheckpointSaver
    from .pause_runtime import restore_closed_state
    from .state import StateError, validate_state
    from .turn_boundary import classify_checkpoint_state

    path = Path(explicit) if explicit else checkpoint_path(run_id,
                                                           artifact_base=artifact_base)
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        threads = document.get("threads") if isinstance(document, dict) else None
        if not isinstance(threads, dict):
            raise ValueError("the checkpoint store holds no thread index")
        saver = FileCheckpointSaver(path)
        found: list[_Head] = []
        for thread_id, thread in sorted(threads.items()):
            if not isinstance(thread, dict) or thread.get("retired"):
                continue
            namespaces = thread.get("namespaces")
            for namespace in sorted(namespaces if isinstance(namespaces, dict) else {}):
                head = saver.head(str(thread_id), checkpoint_ns=str(namespace))
                if not head:
                    continue
                committed = saver.get_tuple({"configurable": {
                    "thread_id": str(thread_id), "checkpoint_ns": str(namespace),
                    "checkpoint_id": head}})
                if committed is None:
                    continue
                state = dict(validate_state(
                    restore_closed_state(committed.checkpoint.get("channel_values") or {}),
                    expected_thread_id=str(thread_id)))
                if str(state.get("run_id") or "") != run_id:
                    continue
                status, next_node = classify_checkpoint_state(state, route=routing.route)
                found.append(_Head(str(thread_id), str(namespace), head, status,
                                   next_node, state))
    except (CheckpointStoreError, StateError, OSError, ValueError, KeyError,
            TypeError) as exc:
        raise pause_policy.PauseRefused(
            "PAUSE_RECORD_CORRUPT",
            f"{run_id}: the durable workflow checkpoint at {path} could not be read "
            f"({exc}); an unreadable authority is not an absent one") from exc
    if not found:
        return None
    statuses = {entry.run_status for entry in found}
    if len(statuses) > 1:
        raise pause_policy.PauseRefused(
            "PAUSE_PROJECTION_DIVERGED",
            f"{run_id}: live threads disagree about the run's status ({sorted(statuses)});"
            " the engine does not pick one")
    return found[0]


def _stale_active_codes(state: Mapping[str, Any], *,
                        current_repository: Mapping[str, Any],
                        current_artifact: Mapping[str, Any]) -> tuple[str, ...]:
    """Revalidation TRIGGERS for an active run, never refusals.

    The same two comparisons ``pause_policy.stale_source_codes`` makes, against the head
    state's OWN committed bindings -- an active run has no ``pause_binding`` to freeze,
    which is why that function cannot be reused verbatim here.  ``STALE_POLICY_DIGEST``
    is deliberately not reported on this branch: an active checkpoint commits no policy
    digest, and reporting a comparison against nothing would be an invention.
    """
    codes: list[str] = []
    if current_repository and dict(state.get("repository_binding") or {}) != \
            contracts.normalize_repository_binding(dict(current_repository)):
        codes.append("STALE_SOURCE_BINDING")
    if current_artifact and dict(state.get("artifact_binding") or {}) != \
            contracts.normalize_artifact_binding(dict(current_artifact)):
        codes.append("STALE_ARTIFACT_BINDING")
    return tuple(codes)


# ---- the paused branch: DELEGATED to OS-31, never forked ------------------------------
_RESUME_STATUS_TO_OUTCOME = {
    "RESUMED": RECOVERED,
    "ALREADY_APPLIED": NO_EFFECT,
    "NO_EFFECT": NO_EFFECT,
}


def _translate_resume(outcome: Any, *, recovery_id: str) -> RecoveryOutcome:
    """Project one ``pause_runtime.ResumeOutcome`` onto the closed outcome set.

    A translation, not a second policy: every branch below reads what OS-31 decided and
    renames nothing.  The default arm is REFUSED rather than a guess, and an unknown code
    would raise at :class:`RecoveryOutcome` construction rather than escape as prose.
    """
    status = str(outcome.status)
    code = str(outcome.code or "")
    if status in _RESUME_STATUS_TO_OUTCOME:
        mapped = _RESUME_STATUS_TO_OUTCOME[status]
        if mapped is RECOVERED:
            code = code or pause_policy.RECOVERY_ADVANCED
        elif status == "ALREADY_APPLIED":
            code = code or "RESPONSE_ALREADY_APPLIED"
        return RecoveryOutcome(
            mapped, code, recovery_id=recovery_id,
            recovery_kind=RECOVERY_KIND_PAUSE_CONTINUATION, detail=outcome.detail,
            resumed_checkpoint_id=outcome.resumed_checkpoint_id if mapped is RECOVERED
            else "",
            effect_performed=bool(outcome.effect_performed) if mapped is RECOVERED
            else False,
            revalidation_codes=tuple(outcome.revalidation_codes),
            next_pause_record=(outcome.next_pause_record if mapped is RECOVERED
                               else None))
    # REFUSED, from OS-31's own closed refusal vocabulary.
    for bucket in (CONFLICT, NOT_RECOVERABLE, UNSUPPORTED):
        if code in RECOVERY_OUTCOME_CODES[bucket]:
            return RecoveryOutcome(bucket, code, recovery_id=recovery_id,
                                   recovery_kind=RECOVERY_KIND_PAUSE_CONTINUATION,
                                   detail=outcome.detail)
    return RecoveryOutcome(REFUSED, code or "PAUSE_LIFECYCLE_INCOHERENT",
                           recovery_id=recovery_id,
                           recovery_kind=RECOVERY_KIND_PAUSE_CONTINUATION,
                           detail=outcome.detail)


# ---- the API ---------------------------------------------------------------------------
def recover_stalled_run(request: RecoveryRequest, *,
                        store: Any = None,
                        saver: Any = None,
                        keeper_factory: Any = None,
                        clock: Any = None) -> RecoveryOutcome:
    """Recover one stalled run, exactly once, and report one closed outcome.

    The four keyword parameters are PORT INSTANCES for test injection -- the claim
    authority, the checkpoint store, lease renewal and the lease clock -- each with an
    existing precedent (``resume_run``'s ``store``/``keeper_factory``,
    ``pause_runtime.py:687``, ``:689``; ``LeaseClockPort``, ``ports.py:178-186``).  None
    of them is a decision, and none of them can express one.
    """
    run_id, base = request.run_id, request.artifact_base
    if request.actor_type not in ("service", "human"):
        raise ValueError(f"unknown actor type: {request.actor_type!r}")

    if not langgraph_available():
        # Refused BEFORE any claim is taken, exactly as ``run_pause_cli`` refuses
        # ``resume`` first (``launcher.py:615-620``).  A missing runtime is never
        # reported as "not recoverable": nothing was established either way.
        return RecoveryOutcome(UNSUPPORTED, "LANGGRAPH_DEPENDENCY_MISSING",
                               detail=f"{run_id}: the pinned LangGraph runtime is absent")

    pause_path = pause_store.pause_record_path(run_id, artifact_base=base)
    recovery_path = recovery_store.recovery_record_path(run_id, artifact_base=base)

    # ---- two run-scoped leases on one run is refused, never resolved (AC-4, DR-3) ----
    if pause_path.is_file() and recovery_path.is_file():
        try:
            foreign = recovery_store.store_for(run_id, artifact_base=base,
                                               clock=clock).read(run_id)
        except recovery_store.RecoveryRecordCorrupt as exc:
            return RecoveryOutcome(REFUSED, "RECOVERY_RECORD_CORRUPT", detail=str(exc))
        lease_clock = clock or recovery_store.SystemLeaseClock()
        if foreign is not None and foreign["status"] == "ACTIVE" \
                and foreign["lease_expires_at"] > lease_clock.time():
            return RecoveryOutcome(
                CONFLICT, "RECOVERY_CLAIM_HELD",
                detail=(f"{run_id}: the run holds an OS-31 pause record AND a live "
                        f"recovery lease owned by {foreign['owner_id']!r}; two "
                        "run-scoped leases on one run is refused, never resolved"))

    if pause_path.is_file():
        return _recover_paused(request, store=store, keeper_factory=keeper_factory)
    return _recover_active(request, store=store, saver=saver,
                           keeper_factory=keeper_factory, clock=clock)


def _recover_paused(request: RecoveryRequest, *, store: Any,
                    keeper_factory: Any) -> RecoveryOutcome:
    """The paused branch.  OS-31 remains the single authority (CON-2).

    Only a continuation a dead process already COMMITTED is recoverable here.  An
    unanswered pause is a human's open decision, and finishing it is not an engine
    action at all -- NG-5.  So this branch classifies the head with OS-31's own
    ``classify_head`` and delegates to ``resume_run`` on exactly one verdict; every other
    verdict is reported, never worked around.
    """
    from . import pause_runtime

    run_id, base = request.run_id, request.artifact_base
    pause_state = store or pause_store.store_for(run_id, artifact_base=base)
    try:
        record = pause_state.read(run_id)
    except pause_store.PauseStoreError as exc:
        return RecoveryOutcome(REFUSED, "PAUSE_RECORD_CORRUPT", detail=str(exc))
    if record is None:
        return RecoveryOutcome(REFUSED, "PAUSE_RECORD_MISSING",
                               detail=f"{run_id}: no pause record")
    if record["status"] != "WAITING_FOR_INPUT":
        return RecoveryOutcome(NO_EFFECT, f"RUN_ALREADY_{record['status']}",
                               recovery_kind=RECOVERY_KIND_PAUSE_CONTINUATION,
                               detail=f"{run_id}: the record is {record['status']}")
    from .checkpoint_store import CheckpointStoreError
    try:
        saver = pause_runtime.open_saver(record, artifact_base=base)
        head = saver.head(record["thread_id"],
                          checkpoint_ns=record["checkpoint_ns"]) or record["checkpoint_id"]
        classified = pause_runtime.classify_head(record, saver)
    except pause_policy.PauseRefused as exc:
        bucket = (NOT_RECOVERABLE if exc.code in RECOVERY_OUTCOME_CODES[NOT_RECOVERABLE]
                  else REFUSED)
        return RecoveryOutcome(bucket, exc.code,
                               recovery_kind=RECOVERY_KIND_PAUSE_CONTINUATION,
                               detail=exc.detail)
    except CheckpointStoreError as exc:
        return RecoveryOutcome(REFUSED, "PAUSE_RECORD_CORRUPT",
                               recovery_kind=RECOVERY_KIND_PAUSE_CONTINUATION,
                               detail=str(exc))
    recovery_id = recovery_identity(
        run_id=run_id, thread_id=record["thread_id"],
        checkpoint_ns=record["checkpoint_ns"], head_checkpoint_id=head,
        recovery_kind=RECOVERY_KIND_PAUSE_CONTINUATION)
    if classified.verdict != pause_policy.PAUSE_CONTINUATION_RECOVERABLE:
        return RecoveryOutcome(
            NOT_RECOVERABLE, "RECOVERY_NO_RUNNABLE_NODE", recovery_id=recovery_id,
            recovery_kind=RECOVERY_KIND_PAUSE_CONTINUATION,
            detail=(f"{run_id}: the head classifies {classified.verdict!r}; only a "
                    "continuation a previous process already committed is recoverable "
                    "without a human answer"))

    projection = record.get("projection") or {}
    kwargs: dict[str, Any] = {}
    if keeper_factory is not None:
        kwargs["keeper_factory"] = keeper_factory
    if store is not None:
        kwargs["store"] = store
    outcome = pause_runtime.resume_run(
        run_id, artifact_base=base, approval_port=request.approval_port,
        graph_factory=request.graph_factory,
        current_repository=dict(request.current_repository
                                or projection.get("repository_binding") or {}),
        current_artifact=dict(request.current_artifact
                              or projection.get("artifact_binding") or {}),
        current_policy_digest=(request.current_policy_digest
                               or str(projection.get("policy_digest") or "")),
        recursion_limit=request.recursion_limit,
        observe_timeout_seconds=request.observe_timeout_seconds, **kwargs)
    return _translate_resume(outcome, recovery_id=recovery_id)


def _recover_active(request: RecoveryRequest, *, store: Any, saver: Any,
                    keeper_factory: Any, clock: Any) -> RecoveryOutcome:
    """The stalled-ACTIVE branch: the run-scoped recovery lease is the claim authority."""
    from .checkpoint_store import CheckpointStoreError, FileCheckpointSaver
    from .lease_keeper import LeaseKeeper
    from .turn_boundary import settle_or_release

    run_id, base = request.run_id, request.artifact_base
    try:
        head = resolve_head(run_id, artifact_base=base)
    except pause_policy.PauseRefused as exc:
        return RecoveryOutcome(REFUSED, exc.code, detail=exc.detail)
    if head is None:
        return RecoveryOutcome(
            NOT_RECOVERABLE, "RECOVERY_HEAD_MISSING",
            recovery_kind=RECOVERY_KIND_STALLED_ACTIVE,
            detail=f"{run_id}: the run has no committed head to continue from")
    if not head.next_node:
        # The head's OWN routing owes nothing: a terminal run, or one already waiting on
        # a human.  Not a defect, and never a licence to invoke anyway.
        return RecoveryOutcome(
            NOT_RECOVERABLE, "RECOVERY_NO_RUNNABLE_NODE",
            recovery_kind=RECOVERY_KIND_STALLED_ACTIVE,
            detail=(f"{run_id}: the committed head reports run_status="
                    f"{head.run_status!r} and its own routing owes no next node"))

    recovery_id = recovery_identity(run_id=run_id, thread_id=head.thread_id,
                                    checkpoint_ns=head.checkpoint_ns,
                                    head_checkpoint_id=head.head_checkpoint_id,
                                    recovery_kind=RECOVERY_KIND_STALLED_ACTIVE)
    lease_store = store or recovery_store.store_for(run_id, artifact_base=base,
                                                    clock=clock)
    # ---- ONE claim attempt.  A loser observes and performs NO effect at any point. ----
    try:
        claimed = dict(lease_store.claim(run_id, thread_id=head.thread_id,
                                         checkpoint_ns=head.checkpoint_ns,
                                         now_iso=_now(),
                                         owner_kind=recovery_store.OWNER_KIND_RECOVERY))
    except recovery_store.RecoveryAuthorityHeld as exc:
        # A LIVE Coordinator owns this run.  There is nothing to observe and nothing to
        # take over: it is not a crashed peer, it is the owner, and the window this closes
        # is exactly the one where it revived after the Watchdog looked.  This claimant
        # therefore NEITHER waits NOR proceeds -- it fails closed here, having performed
        # nothing, with the one stable code that names why.
        return RecoveryOutcome(CONFLICT, recovery_store.EXECUTION_AUTHORITY_HELD,
                               recovery_id=recovery_id,
                               recovery_kind=RECOVERY_KIND_STALLED_ACTIVE,
                               detail=str(exc))
    except recovery_store.RecoveryClaimHeld as exc:
        try:
            settled = lease_store.observe(
                run_id,
                timeout_seconds=(request.observe_timeout_seconds
                                 if request.observe_timeout_seconds is not None
                                 else pause_store.observe_timeout_for(
                                     getattr(lease_store, "lease_seconds",
                                             recovery_store.DEFAULT_LEASE_SECONDS))))
        except recovery_store.RecoveryClaimHeld as timeout:
            return RecoveryOutcome(CONFLICT, "RECOVERY_CLAIM_HELD",
                                   recovery_id=recovery_id,
                                   recovery_kind=RECOVERY_KIND_STALLED_ACTIVE,
                                   detail=str(timeout))
        if settled is not None:
            return RecoveryOutcome(NO_EFFECT, pause_policy.RECOVERY_ALREADY_APPLIED,
                                   recovery_id=recovery_id,
                                   recovery_kind=RECOVERY_KIND_STALLED_ACTIVE,
                                   detail=f"{run_id}: the winner settled this run")
        del exc
        try:                                     # the lease lapsed: ONE takeover attempt
            claimed = dict(lease_store.claim(
                run_id, thread_id=head.thread_id, checkpoint_ns=head.checkpoint_ns,
                now_iso=_now(), owner_kind=recovery_store.OWNER_KIND_RECOVERY))
        except recovery_store.RecoveryAuthorityHeld as owned:
            # A Coordinator took the run while this claimant was observing.  Same rule.
            return RecoveryOutcome(CONFLICT, recovery_store.EXECUTION_AUTHORITY_HELD,
                                   recovery_id=recovery_id,
                                   recovery_kind=RECOVERY_KIND_STALLED_ACTIVE,
                                   detail=str(owned))
        except recovery_store.RecoveryClaimHeld as again:
            return RecoveryOutcome(CONFLICT, "RECOVERY_CLAIM_HELD",
                                   recovery_id=recovery_id,
                                   recovery_kind=RECOVERY_KIND_STALLED_ACTIVE,
                                   detail=str(again))
    except recovery_store.RecoveryRecordCorrupt as exc:
        return RecoveryOutcome(REFUSED, "RECOVERY_RECORD_CORRUPT", recovery_id=recovery_id,
                               recovery_kind=RECOVERY_KIND_STALLED_ACTIVE, detail=str(exc))
    if claimed["claim_outcome"] == recovery_store.ALREADY_SETTLED:
        return RecoveryOutcome(NO_EFFECT, pause_policy.RECOVERY_ALREADY_APPLIED,
                               recovery_id=recovery_id,
                               recovery_kind=RECOVERY_KIND_STALLED_ACTIVE,
                               detail=f"{run_id}: this run's recovery is already settled")

    lease_token = claimed["lease_token"]
    # ---- OS-43 F-001 (iteration 5): the HELD SECTION, and it starts HERE ---------------
    # Past this line this Watchdog owns the run's execution authority, so past this line
    # every path -- the already-promoted short-circuit, a keeper factory that raises, a
    # refusal, a crash, and success -- leaves through ONE `finally`, and that `finally`
    # closes the hold through the SAME `turn_boundary.settle_or_release` the Coordinator
    # uses.  Before this, the successful path fell through an unconditional `release`, so
    # a Watchdog that FINISHED a run left the record ACTIVE and a later Coordinator
    # restart took the completed run as new work.
    checkpoint = None
    revalidation: tuple[str, ...] = ()
    try:
        stored = (claimed.get("attempts") or {}).get(recovery_id)
        if stored is not None and stored["stage"] == "PROMOTED":
            # The identity is already promoted: the run has not moved since a completed
            # attempt, so there is nothing to do and no second effect to perform.  This
            # branch is only REACHABLE for a non-terminal head -- `resolve_head` above
            # refuses `RECOVERY_NO_RUNNABLE_NODE` before any claim when the committed head
            # owes no next node -- so the shared discipline necessarily RELEASES here, and
            # says so for the same reason the Coordinator's does rather than by omission.
            return RecoveryOutcome(NO_EFFECT, pause_policy.RECOVERY_ALREADY_APPLIED,
                                   recovery_id=recovery_id,
                                   recovery_kind=RECOVERY_KIND_STALLED_ACTIVE,
                                   head_before=stored["head_before"],
                                   head_after=stored["head_after"],
                                   detail=f"{run_id}: attempt {recovery_id} is promoted")

        factory = keeper_factory or _default_keeper_factory()
        revalidation = _stale_active_codes(
            head.state, current_repository=request.current_repository,
            current_artifact=request.current_artifact)
        with factory(lease_store, run_id, lease_token) as keeper:
            checkpoint = saver or FileCheckpointSaver(checkpoint_path(run_id,
                                                                      artifact_base=base))
            # ---- the write-before-effect boundary, copied verbatim -------------------
            # The stage may be ahead of the checkpoint -- harmless, the attempt is then
            # re-driven byte-identically -- but the checkpoint must NEVER be ahead of the
            # stage (``pause_runtime.py:790-795``).
            lease_store.open_attempt(run_id, {
                "recovery_id": recovery_id,
                "recovery_kind": RECOVERY_KIND_STALLED_ACTIVE, "stage": "CLAIMED",
                "head_before": head.head_checkpoint_id, "head_after": "",
                "outcome": "", "code": "", "actor_id": request.actor_id,
                "opened_at": _now(), "promoted_at": None}, lease_token=lease_token)
            keeper.raise_if_lost()
            # R4.  The token is validated again HERE, atomically, immediately before the
            # transition -- and the same fence is carried on the checkpoint store the
            # graph is built over, so every node that creates an external effect
            # revalidates it too (``graph.build_graph``).  A caller-supplied
            # ``graph_factory`` needs no new parameter to inherit it: it already receives
            # this saver and hands it straight to ``build_graph``.
            def _fence(_run: str = run_id, _token: str = lease_token) -> None:
                lease_store.fence(_run, _token)

            _fence()
            checkpoint.execution_fence = _fence
            graph = request.graph_factory(checkpoint)
            if graph is None or not hasattr(graph, "invoke"):
                # Second line of defence behind the port's own resolution.  The attempt
                # entry stays CLAIMED, no effect is performed, and the caller is told BY
                # NAME that this run has no runnable graph -- an ``AttributeError`` here
                # is indistinguishable from a recovery that ran and failed.
                raise ports.RecoveryPreconditionUnavailable(
                    ports.RECOVERY_GRAPH_UNAVAILABLE,
                    f"{run_id}: the graph factory produced no runnable graph")
            config: dict[str, Any] = {"configurable": {
                "thread_id": head.thread_id, "checkpoint_ns": head.checkpoint_ns}}
            if request.recursion_limit:
                config["recursion_limit"] = request.recursion_limit
            before = checkpoint.head(head.thread_id,
                                     checkpoint_ns=head.checkpoint_ns) or ""
            # ``invoke(None, config)`` resumes the thread from its committed head, so
            # LangGraph decides what work remains: every superstep whose result the
            # checkpoint already holds is not re-run.
            graph.invoke(None, config)
            keeper.raise_if_lost()
            _fence()
            after = checkpoint.head(head.thread_id,
                                    checkpoint_ns=head.checkpoint_ns) or ""
            performed = after != before
            lease_store.promote_attempt(run_id, recovery_id, head_after=after,
                                        outcome=RECOVERED,
                                        code=pause_policy.RECOVERY_ADVANCED,
                                        promoted_at=_now(), lease_token=lease_token)
    except LeaseRenewalFailed as exc:
        # Fail closed and STAY closed: a successor may already own the run.
        return RecoveryOutcome(CONFLICT, "RECOVERY_CLAIM_LOST", recovery_id=recovery_id,
                               recovery_kind=RECOVERY_KIND_STALLED_ACTIVE, detail=str(exc))
    except recovery_store.RecoveryClaimLost as exc:
        return RecoveryOutcome(CONFLICT, "RECOVERY_CLAIM_LOST", recovery_id=recovery_id,
                               recovery_kind=RECOVERY_KIND_STALLED_ACTIVE, detail=str(exc))
    except recovery_store.RecoveryRecordCorrupt as exc:
        return RecoveryOutcome(REFUSED, "RECOVERY_RECORD_CORRUPT", recovery_id=recovery_id,
                               recovery_kind=RECOVERY_KIND_STALLED_ACTIVE, detail=str(exc))
    except pause_policy.PauseRefused as exc:
        bucket = (NOT_RECOVERABLE if exc.code in RECOVERY_OUTCOME_CODES[NOT_RECOVERABLE]
                  else REFUSED)
        return RecoveryOutcome(bucket, exc.code, recovery_id=recovery_id,
                               recovery_kind=RECOVERY_KIND_STALLED_ACTIVE,
                               detail=exc.detail)
    except CheckpointStoreError as exc:
        return RecoveryOutcome(REFUSED, "PAUSE_RECORD_CORRUPT", recovery_id=recovery_id,
                               recovery_kind=RECOVERY_KIND_STALLED_ACTIVE, detail=str(exc))
    finally:
        # The fence is scoped to the lease it validates, so it does not outlive it on an
        # injected saver the caller may reuse.
        if checkpoint is not None and hasattr(checkpoint, "execution_fence"):
            del checkpoint.execution_fence
        # The one way this role gives up the authority, and it is the SAME function the
        # Coordinator gives it up through: a run whose own committed head says it FINISHED
        # is SETTLED, and everything else -- interrupted, paused, unreadable, refused,
        # crashed, or a checkpointer this attempt never even built (`checkpoint is None`)
        # -- is RELEASED and stays recoverable.  The explicit `release` calls the two
        # refusal branches above used to make are gone WITH their branches, not replaced
        # beside them: two ways to end one hold is how the Coordinator half and the
        # Watchdog half came to disagree in the first place.
        settle_or_release(lease_store, run_id, lease_token, checkpointer=checkpoint,
                          thread_id=head.thread_id, checkpoint_ns=head.checkpoint_ns)
    return RecoveryOutcome(RECOVERED, pause_policy.RECOVERY_ADVANCED,
                           recovery_id=recovery_id,
                           recovery_kind=RECOVERY_KIND_STALLED_ACTIVE,
                           resumed_checkpoint_id=after, effect_performed=performed,
                           head_before=before, head_after=after,
                           revalidation_codes=revalidation)


def _default_keeper_factory() -> Any:
    from .lease_keeper import lease_keeper_factory
    return lease_keeper_factory(
        interval_seconds=heartbeat_interval_for(recovery_store.DEFAULT_LEASE_SECONDS))


# ---- U-3: the discovery surface for runs with NO pause record --------------------------
def discover_recoverable_runs(artifact_base: str | os.PathLike[str], *,
                              langgraph_available: bool = True
                              ) -> tuple[dict[str, Any], ...]:
    """Every run a supervisor may have to consider, with the verdict the API will act on.

    Symmetric with ``pause_store.discover_paused_runs`` (``pause_store.py:729-767``) and
    deliberately WIDER: that function ``continue``s past every directory with no pause
    record (``pause_store.py:742-744``), so a stalled ACTIVE run is invisible to it.
    Read-only: it takes no claim and performs no effect, the discipline
    ``pause_runtime.discover`` states at ``pause_runtime.py:538-540``.

    A run root that cannot be read is REPORTED with a verdict naming the defect, never
    omitted.  An unreadable RUNS ROOT raises :class:`RunsRootUnreadable`: "unknown" is not
    "empty" (``ports.py:141-148``).
    """
    from . import pause_runtime

    root = Path(artifact_base) / "artifacts" / "runs"
    if not root.exists():
        return ()
    try:
        children = sorted(root.iterdir())
    except OSError as exc:
        raise RunsRootUnreadable(
            f"the runs root {root} could not be listed ({exc}); an unreadable authority "
            "is not an absent one, so no discovery listing is reported") from exc

    paused = {row["run_id"]: row for row in pause_runtime.discover(
        artifact_base, langgraph_available=langgraph_available)}
    listings: list[dict[str, Any]] = []
    for run_dir in children:
        run_id = run_dir.name
        if not run_dir.is_dir():
            continue
        if run_id in paused:
            row = paused[run_id]
            listings.append(_row(run_id, kind=RECOVERY_KIND_PAUSE_CONTINUATION,
                                 status=row.get("status") or "",
                                 verdict=row.get("verdict") or "",
                                 detail=row.get("detail") or "",
                                 pause_record_id=row.get("pause_record_id") or "",
                                 owner_id=row.get("owner_id") or "",
                                 head_checkpoint_id=row.get("checkpoint_id") or ""))
            continue
        try:
            readable = any(True for _ in run_dir.iterdir())
        except OSError as exc:
            listings.append(_row(run_id, kind="unknown", verdict=RUN_ROOT_UNREADABLE,
                                 detail=str(exc)))
            continue
        del readable
        if not checkpoint_path(run_id, artifact_base=artifact_base).is_file():
            listings.append(_row(run_id, kind="unknown", verdict="NO_CHECKPOINT_AUTHORITY",
                                 detail="the run holds no OS-40 checkpoint store"))
            continue
        if not langgraph_available:
            # Degraded and NAMED as such.  A missing runtime is never reported as
            # recoverable and never as "the run is fine".
            listings.append(_row(run_id, kind=RECOVERY_KIND_STALLED_ACTIVE,
                                 verdict="CHECKPOINT_UNVERIFIED",
                                 detail="LangGraph is absent; the head was not evaluated"))
            continue
        try:
            head = resolve_head(run_id, artifact_base=artifact_base)
        except pause_policy.PauseRefused as exc:
            listings.append(_row(run_id, kind=RECOVERY_KIND_STALLED_ACTIVE,
                                 verdict=exc.code, detail=exc.detail))
            continue
        if head is None:
            listings.append(_row(run_id, kind=RECOVERY_KIND_STALLED_ACTIVE,
                                 verdict="RECOVERY_HEAD_MISSING",
                                 detail="the store holds no committed head for this run"))
            continue
        verdict = (RECOVERY_STALLED_RECOVERABLE if head.next_node
                   else "RECOVERY_NO_RUNNABLE_NODE")
        listings.append(_row(run_id, kind=RECOVERY_KIND_STALLED_ACTIVE,
                             status=head.run_status, verdict=verdict,
                             thread_id=head.thread_id, checkpoint_ns=head.checkpoint_ns,
                             head_checkpoint_id=head.head_checkpoint_id,
                             next_node=head.next_node))
    return tuple(listings)


def _row(run_id: str, *, kind: str, status: str = "", verdict: str = "", detail: str = "",
         thread_id: str = "", checkpoint_ns: str = "", head_checkpoint_id: str = "",
         next_node: str = "", pause_record_id: str = "", owner_id: str = "",
         ) -> dict[str, Any]:
    if verdict and verdict not in DISCOVERY_VERDICTS:
        raise ValueError(f"discovery verdict outside the closed set: {verdict!r}")
    return {"run_id": run_id, "recovery_kind": kind, "status": status,
            "verdict": verdict, "detail": detail, "thread_id": thread_id,
            "checkpoint_ns": checkpoint_ns, "head_checkpoint_id": head_checkpoint_id,
            "next_node": next_node, "pause_record_id": pause_record_id,
            "owner_id": owner_id}


# ======================================================================================
# The CONCRETE port implementations.
#
# They live here, on the engine side, and deliberately NOT in the supervisor package:
# they legitimately open the pause store, the checkpoint store and the recovery lease, and
# the Watchdog core must not be able to reach any of those.  Keeping them here is what
# makes the core's import closure assertable (CON-1 / T-6) -- the core holds a port
# instance, never a module that can claim anything.  Their access is read-only except
# through ``recover_stalled_run`` itself.
# ======================================================================================
class RunDiscovery:
    """:class:`ports.RunDiscoveryPort` over :func:`discover_recoverable_runs`."""

    def __init__(self, artifact_base: str | os.PathLike[str] = ".", *,
                 langgraph: bool | None = None) -> None:
        self.artifact_base = Path(artifact_base)
        self._langgraph = langgraph

    def discover(self) -> tuple[Mapping[str, Any], ...]:
        available = (langgraph_available() if self._langgraph is None
                     else bool(self._langgraph))
        return discover_recoverable_runs(self.artifact_base,
                                         langgraph_available=available)


class CoordinatorLivenessReader:
    """:class:`ports.CoordinatorLivenessPort`.  Read-only, four-valued, never boolean."""

    def __init__(self, artifact_base: str | os.PathLike[str] = ".", *,
                 clock: Any = None) -> None:
        self.artifact_base = Path(artifact_base)
        self.clock = clock

    def status(self, run_id: str) -> str:
        from . import coordinator_liveness
        return coordinator_liveness.liveness_status(run_id,
                                                    artifact_base=self.artifact_base,
                                                    clock=self.clock)

    def record(self, run_id: str) -> Mapping[str, Any] | None:
        from . import coordinator_liveness
        try:
            return coordinator_liveness.liveness_record(run_id,
                                                        artifact_base=self.artifact_base)
        except coordinator_liveness.CoordinatorLivenessError:
            return None


class RunObservationAdapter:
    """:class:`ports.RunObservationPort` over this repository's durable authorities.

    Every method reports a legitimately ABSENT authority as an absence and RAISES
    ``ObservationUnavailable`` when an authority exists and cannot be read; a fact no
    authority covers at all raises ``ObservationUnsupported``.  The port never collapses
    the three -- turning a refusal into F1 and an uncovered fact into F11 is the snapshot
    builder's job, so no implementation of this port ever has to lie.
    """

    def __init__(self, artifact_base: str | os.PathLike[str] = ".", *,
                 runner: Any = None, capabilities: Any = None, clock: Any = None,
                 owner_id: str | None = None) -> None:
        self.artifact_base = Path(artifact_base)
        self.runner = runner
        self._capabilities = capabilities
        self.clock = clock
        self.owner_id = owner_id

    # -- F5 / F6 / F7 -------------------------------------------------------------
    def orca_state(self, run_id: str) -> Mapping[str, Any]:
        from .watchdog_observation import ObservationUnavailable, ObservationUnsupported
        from . import turn_boundary
        if self.runner is None:
            # No Orca listing authority is wired for this runtime at all.  That is
            # UNSUPPORTED, not "no dispatch is running".
            raise ObservationUnsupported(f"{run_id}: no Orca listing authority")
        try:
            state = turn_boundary.observe_orca_state(run_id, runner=self.runner)
        except turn_boundary.TurnBoundaryUnavailable as exc:
            raise ObservationUnavailable(str(exc)) from exc
        return {"active_dispatches": tuple(state["active_dispatches"]),
                "runnable_actions": tuple(state["runnable_actions"])}

    # -- F2 / F5 ------------------------------------------------------------------
    def checkpoint_state(self, run_id: str) -> Mapping[str, Any]:
        from .watchdog_observation import ObservationUnavailable
        absent = {"present": False, "run_status": "", "next_node": "", "thread_id": "",
                  "checkpoint_ns": "", "head_checkpoint_id": "", "status_authority": ""}
        if not langgraph_available():
            # The store may exist and cannot be evaluated: unreadable, never absent.
            if checkpoint_path(run_id, artifact_base=self.artifact_base).is_file():
                raise ObservationUnavailable(
                    f"{run_id}: a durable checkpoint exists but LangGraph is absent")
            return absent
        try:
            head = resolve_head(run_id, artifact_base=self.artifact_base)
        except pause_policy.PauseRefused as exc:
            raise ObservationUnavailable(f"{run_id}: {exc.detail}") from exc
        if head is None:
            return absent
        from .turn_boundary import STATUS_AUTHORITY_CHECKPOINT
        return {"present": True, "run_status": head.run_status,
                "next_node": head.next_node, "thread_id": head.thread_id,
                "checkpoint_ns": head.checkpoint_ns,
                "head_checkpoint_id": head.head_checkpoint_id,
                "status_authority": STATUS_AUTHORITY_CHECKPOINT}

    # -- F2 / F4 ------------------------------------------------------------------
    def pause_state(self, run_id: str) -> Mapping[str, Any] | None:
        from .watchdog_observation import ObservationUnavailable
        from . import pause_runtime
        path = pause_store.pause_record_path(run_id, artifact_base=self.artifact_base)
        if not path.is_file():
            return None                       # verifiably absent: ABSENT_DECLARED
        try:
            record = pause_store.store_for(run_id,
                                           artifact_base=self.artifact_base).read(run_id)
        except pause_store.PauseStoreError as exc:
            raise ObservationUnavailable(f"{run_id}: {exc}") from exc
        if record is None:
            return None
        verdict = ""
        if record["status"] == "WAITING_FOR_INPUT" and langgraph_available():
            from .checkpoint_store import CheckpointStoreError
            try:
                saver = pause_runtime.open_saver(record, artifact_base=self.artifact_base)
                verdict = pause_runtime.classify_head(record, saver).verdict
            except pause_policy.PauseRefused as exc:
                verdict = ""                  # a named refusal, not an actionable verdict
                del exc
            except CheckpointStoreError as exc:
                raise ObservationUnavailable(f"{run_id}: {exc}") from exc
        return {"status": record["status"], "verdict": verdict,
                "thread_id": record["thread_id"],
                "checkpoint_ns": record["checkpoint_ns"],
                "checkpoint_id": record["checkpoint_id"]}

    # -- F3, TRI-VALUED and deliberately NEW code ---------------------------------
    def durable_wait(self, run_id: str) -> Mapping[str, Any]:
        """Which durable artefacts prove a human wait is armed, and which could not answer.

        This must NOT reuse ``turn_boundary.observe_durable_wait``: each of its three
        witnesses fails OPEN (``turn_boundary.py:569-572``, ``:595-598``, ``:601-608``).
        That is safe for a turn end, which only refuses a turn, and unsafe here, where an
        empty result would read as "no human wait armed" and auto-resume one.  So each
        witness reports itself as evidence OR as unreadable, and never as silence.
        """
        import json

        from . import turn_boundary
        evidence: list[str] = []
        unreadable: list[str] = []
        record_path = (self.artifact_base / "artifacts" / "runs" / run_id
                       / pause_store.PAUSE_RECORD_FILENAME)
        if record_path.is_file():
            try:
                document = json.loads(record_path.read_text(encoding="utf-8"))
                status = ((document or {}).get("record") or {}).get("status")
                if status == "WAITING_FOR_INPUT":
                    evidence.append(turn_boundary.WAIT_EVIDENCE_PAUSE_RECORD)
                elif not isinstance(document, dict) or "record" not in document:
                    unreadable.append(turn_boundary.WAIT_EVIDENCE_PAUSE_RECORD)
            except (OSError, ValueError):
                unreadable.append(turn_boundary.WAIT_EVIDENCE_PAUSE_RECORD)
        requests = (self.artifact_base / "artifacts" / "runs" / run_id
                    / "clarifications" / "requests")
        if requests.exists():
            try:
                if any(child.is_dir() for child in requests.iterdir()):
                    evidence.append(turn_boundary.WAIT_EVIDENCE_CLARIFICATION)
            except OSError:
                unreadable.append(turn_boundary.WAIT_EVIDENCE_CLARIFICATION)
        if self.runner is not None:
            try:
                gates = turn_boundary._orca_json(
                    ("orchestration", "gate-list", "--run", run_id, "--json"),
                    runner=self.runner).get("gates")
            except turn_boundary.TurnBoundaryUnavailable:
                unreadable.append(turn_boundary.WAIT_EVIDENCE_DECISION_GATE)
                gates = None
            for gate in gates or []:
                if isinstance(gate, dict) and str(gate.get("status") or "") not in {
                        "resolved", "cancelled"}:
                    evidence.append(turn_boundary.WAIT_EVIDENCE_DECISION_GATE)
                    break
        return {"evidence": tuple(evidence), "unreadable": tuple(unreadable)}

    # -- F8 -----------------------------------------------------------------------
    def delivery_obligations(self, run_id: str) -> tuple[str, ...]:
        from .watchdog_observation import ObservationUnavailable
        from . import quiescence
        try:
            from scripts import run_logging
        except ImportError:                   # installed Skill layout
            import run_logging                # type: ignore[no-redef]
        try:
            ledger = run_logging.replay_delivery_ledger(run_id, base=self.artifact_base)
        except (OSError, ValueError) as exc:
            raise ObservationUnavailable(f"{run_id}: {exc}") from exc
        return tuple(delivery_id for delivery_id, row in sorted(ledger.items())
                     if quiescence.delivery_obligation(row) != quiescence.OBLIGATION_NONE)

    # -- F9 -----------------------------------------------------------------------
    def foreign_lease(self, run_id: str) -> Mapping[str, Any] | None:
        """A live run-scoped lease held by SOMEBODY ELSE, or ``None``.

        Both run-scoped authorities are consulted, because which one owns a run is the
        run's own durable state to decide (D-2.5), not this reader's.
        """
        from .watchdog_observation import ObservationUnavailable
        from .runtime_state import SystemLeaseClock, default_owner_id
        clock = self.clock or SystemLeaseClock()
        mine = self.owner_id or default_owner_id()
        try:
            pause_record = pause_store.store_for(
                run_id, artifact_base=self.artifact_base).read(run_id) \
                if pause_store.pause_record_path(
                    run_id, artifact_base=self.artifact_base).is_file() else None
            recovery_record = recovery_store.store_for(
                run_id, artifact_base=self.artifact_base).read(run_id) \
                if recovery_store.recovery_record_path(
                    run_id, artifact_base=self.artifact_base).is_file() else None
        except (pause_store.PauseStoreError,
                recovery_store.RecoveryStoreError) as exc:
            raise ObservationUnavailable(f"{run_id}: {exc}") from exc
        for record in (pause_record, recovery_record):
            if record is None:
                continue
            if record["owner_id"] and record["owner_id"] != mine \
                    and record["lease_expires_at"] > clock.time():
                return {"owner_id": record["owner_id"],
                        "lease_expires_at": record["lease_expires_at"]}
        return None

    # -- F11's capability clause ---------------------------------------------------
    def declared_capabilities(self, run_id: str) -> frozenset[str]:
        from .watchdog_observation import ObservationUnsupported
        if self._capabilities is None:
            raise ObservationUnsupported(f"{run_id}: no capability authority is declared")
        value = (self._capabilities(run_id) if callable(self._capabilities)
                 else self._capabilities)
        return frozenset(value)


class EngineRecoveryInvocation:
    """:class:`ports.RecoveryInvocationPort`.  The supervisor's ONLY route to the engine.

    ``build_request`` assembles the closed :class:`RecoveryRequest` from observations the
    supervisor supplies; there is no parameter here through which a caller could express a
    next node, a verdict, a phase, a decision-bundle id, a lease token or a force flag.
    """

    def __init__(self, *, artifact_base: str | os.PathLike[str] = ".",
                 graph_factory: Any = None, graph_factory_for: Any = None,
                 approval_port: Any = None,
                 current_repository: Mapping[str, Any] | None = None,
                 current_artifact: Mapping[str, Any] | None = None,
                 current_policy_digest: str = "", actor_id: str = "",
                 recursion_limit: int | None = None,
                 observe_timeout_seconds: float | None = None,
                 store: Any = None, saver: Any = None, keeper_factory: Any = None,
                 clock: Any = None) -> None:
        if (graph_factory is None) == (graph_factory_for is None):
            raise ValueError(
                "exactly one of graph_factory (one run) or graph_factory_for (a factory "
                "PER run) is required; a port with neither could only ever hand the "
                "engine a graph it cannot build, and one with both would have two "
                "answers for the same run")
        self.artifact_base = str(artifact_base)
        self.graph_factory = graph_factory
        self.graph_factory_for = graph_factory_for
        self.approval_port = approval_port
        self.current_repository = dict(current_repository or {})
        self.current_artifact = dict(current_artifact or {})
        self.current_policy_digest = current_policy_digest
        self.actor_id = actor_id
        self.recursion_limit = recursion_limit
        self.observe_timeout_seconds = observe_timeout_seconds
        self._store, self._saver = store, saver
        self._keeper_factory, self._clock = keeper_factory, clock

    def identity(self, *, run_id: str, thread_id: str, checkpoint_ns: str,
                 head_checkpoint_id: str, recovery_kind: str) -> str:
        return recovery_identity(run_id=run_id, thread_id=thread_id,
                                 checkpoint_ns=checkpoint_ns,
                                 head_checkpoint_id=head_checkpoint_id,
                                 recovery_kind=recovery_kind)

    def graph_factory_for_run(self, run_id: str) -> Any:
        """The graph factory for THIS run, or a named refusal.  Never ``None``.

        A sweep discovers many runs, and each one resumes its own thread with its own
        ledger, journal and adapter, so a factory chosen once for a whole sweep is either
        wrong for every run but one or -- as the shipped wiring did -- a placeholder that
        returns ``None`` and dies inside ``graph.invoke`` with an ``AttributeError`` the
        supervisor could only report as "something failed after we acted".  Resolution
        therefore happens HERE, per run, at request time, and a run whose graph cannot be
        built is refused by name before anything is claimed.
        """
        if self.graph_factory_for is None:
            return self.graph_factory
        try:
            factory = self.graph_factory_for(run_id)
        except ports.RecoveryPreconditionUnavailable:
            raise
        except Exception as exc:                          # noqa: BLE001 - named below
            raise ports.RecoveryPreconditionUnavailable(
                ports.RECOVERY_GRAPH_UNAVAILABLE,
                f"{run_id}: the wiring could not build a graph for this run "
                f"({type(exc).__name__}: {exc})") from exc
        if factory is None or not callable(factory):
            raise ports.RecoveryPreconditionUnavailable(
                ports.RECOVERY_GRAPH_UNAVAILABLE,
                f"{run_id}: the wiring produced no graph factory for this run")
        return factory

    def build_request(self, *, run_id: str, recovery_kind: str) -> RecoveryRequest:
        if recovery_kind not in RECOVERY_KINDS:
            raise ValueError(f"unknown recovery kind: {recovery_kind!r}")
        return RecoveryRequest(
            run_id=run_id, artifact_base=self.artifact_base,
            graph_factory=self.graph_factory_for_run(run_id),
            current_repository=dict(self.current_repository),
            current_artifact=dict(self.current_artifact),
            current_policy_digest=self.current_policy_digest,
            actor_id=self.actor_id, actor_type="service",
            observe_timeout_seconds=self.observe_timeout_seconds,
            recursion_limit=self.recursion_limit, approval_port=self.approval_port)

    def recover(self, request: RecoveryRequest) -> RecoveryOutcome:
        return recover_stalled_run(request, store=self._store, saver=self._saver,
                                   keeper_factory=self._keeper_factory,
                                   clock=self._clock)
