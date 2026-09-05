"""Ties the two durable tiers together: finalise, discover, take over, resume, dispose.

This is the only new module that opens the Tier-1 saver, and therefore the only new module
that needs LangGraph -- but it needs it lazily, because the documented no-LangGraph
``discover`` fallback reaches this module (see :func:`_checkpoint_store`).  Everything it
decides is decided by :mod:`pause_policy`, which is pure; what lives here is the
*sequencing* -- the commit point of a pause, the C1-C5 consistency rules, the run-scoped
claim fence, and the strict "write the dedupe key before the effect" ordering that makes
resume and disposal exactly-once across a crash.

C5 is the resume-continuation boundary.  A resume commits three things in order -- the
applied bundle (intent), the checkpoint re-entry (continuation), and the promotion
(completion) -- and each pair of them has a crash window between it.  C1-C4 could not tell
"the head moved because THIS run's continuation committed" from "the head is stale", so a
crash in the middle window was refused forever as STALE_CHECKPOINT_HEAD and reindex()
could not repair it either.  C5 tells them apart from durable bytes alone: the applied
stage (``RECORDED`` -> ``CONTINUING`` -> ``RESUMED``) says a continuation may have
committed, and the checkpoint's own head pointer and parent links say whether it did and
how far it got.  A successor then finishes it exactly once -- LangGraph re-runs only the
supersteps whose results the checkpoint does not already hold.

The checkpointed ``WorkflowState`` is the sole reconstruction authority.  The Tier-2
record's ``projection`` is read **only** inside :func:`assert_c3` and is never an input to
``validate_state``; the repair direction is checkpoint -> record and never the reverse.
"""
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import pause_policy, pause_store
from .lease_keeper import LeaseRenewalFailed, lease_keeper_factory
from .pause_policy import PauseRefused
from .state import WorkflowState, validate_state

if TYPE_CHECKING:  # the annotation only -- never an import at run time
    from .checkpoint_store import FileCheckpointSaver

try:
    from scripts import run_logging
except ImportError:  # installed Skill layout exposes sibling tools directly
    import run_logging  # type: ignore[no-redef]

_CLOSED_STATE_FIELDS = tuple(WorkflowState.__required_keys__)

# ---- the only LangGraph dependency in this module, and it is deferred ------------------
# ``discover`` is documented (INSTALL.md, SKILL.md section 17) as working with LangGraph
# absent, and the shipped CLI reaches it through this module.  :mod:`checkpoint_store`
# subclasses ``BaseCheckpointSaver`` and therefore cannot be imported without LangGraph,
# so it is imported at the point of use instead of at module scope.  Nothing about the
# authority of the checkpoint changes: every path that actually reads or writes a
# checkpoint still goes through the real Tier-1 saver, and the degraded verdict remains
# CHECKPOINT_UNVERIFIED rather than RESUMABLE.
_CHECKPOINT_STORE: Any = None


def _checkpoint_store() -> Any:
    """Import :mod:`checkpoint_store` on first use and memoise it.

    Raises ``ModuleNotFoundError`` when LangGraph is absent -- which is correct: every
    caller of this helper is a path that genuinely needs checkpoint authority, and
    fail-closed is the required behaviour there.  Degraded discovery never calls it.
    """
    global _CHECKPOINT_STORE
    if _CHECKPOINT_STORE is None:
        from . import checkpoint_store

        _CHECKPOINT_STORE = checkpoint_store
    return _CHECKPOINT_STORE


def restore_closed_state(values: Mapping[str, Any]) -> dict[str, Any]:
    """Project a LangGraph snapshot back onto exactly the closed ``WorkflowState`` fields.

    Two adjustments, both forced by how LangGraph stores channels and neither of them a
    reinterpretation of the state: a channel whose value is ``None`` is omitted from a
    snapshot, and the graph's own node-trigger channels (``TERMINAL`` and friends) appear
    alongside the state fields.  So the closed set is restored and anything outside it is
    dropped -- exactly as ``graph._merge_checkpoint`` already does for an update.
    """
    restored: dict[str, Any] = {field: None for field in _CLOSED_STATE_FIELDS}
    for field in _CLOSED_STATE_FIELDS:
        if field in values:
            restored[field] = values[field]
    return restored


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- AC-7: append-only audit and timing evidence -------------------------------------
# Every row below is APPENDED; nothing published is ever rewritten.  A logging failure never
# stops a pause, a resume or a disposal -- section 9 does not let a logging concern stop a
# run -- so each helper is best-effort in exactly the way ``_safe_log`` already is.
def _audit(run_id: str, artifact_base: Any, event: str, **columns: Any) -> None:
    try:
        run_logging.log_orchestrator_event(run_id, base=Path(artifact_base), event=event,
                                           **columns)
    except Exception:  # noqa: BLE001 - audit is evidence, never a gate
        pass


def _timing(run_id: str, artifact_base: Any, event: str, **columns: Any) -> None:
    try:
        run_logging.log_timing_event(run_id, base=Path(artifact_base), event=event,
                                     **columns)
    except Exception:  # noqa: BLE001 - audit is evidence, never a gate
        pass


def _run_status(run_id: str, artifact_base: Any, status: str, *, reason: str = "",
                risk: str = "", close_scopes: bool = False) -> None:
    """One ``run_end`` pair, with a non-blank ``started_at`` recovered from the tracker.

    ``.timing_state.json`` is the only place a NEW Coordinator can recover
    ``run_started_at``, which is what stops the OS-19 defect of a blank ``started_at`` and a
    blank ``duration_s`` on a disposal written by a different process.
    """
    try:
        tracker = run_logging.RunTimingTracker.load(run_id, base=Path(artifact_base),
                                                    risk=risk)
        if close_scopes:
            tracker.close_all()
            tracker.save()
        run_logging.log_run_status(run_id, status, base=Path(artifact_base), reason=reason,
                                   run_started_at=tracker.run_started_at, risk=risk)
    except Exception:  # noqa: BLE001 - audit is evidence, never a gate
        pass


# ---- keeping the run-scoped lease alive for the WHOLE claimed section ------------------
# ``claim()`` mints a lease of ``store.lease_seconds`` and nothing renewed it, while the
# claimed section -- read the decision, revalidate, update the checkpoint, and above all
# ``graph.invoke()`` -- runs for as long as the resumed run takes.  A healthy owner was
# therefore indistinguishable from a dead one after one lease period: its lease lapsed, a
# second Coordinator legally claimed the same RECORDED bundle, saw a checkpoint that still
# said WAITING_FOR_INPUT, and re-entered the same run concurrently.
#
# This is the identical defect ``lease_keeper`` already solves for the executor's blocking
# adapter calls, so it is solved the identical way rather than with a second mechanism:
# :class:`lease_keeper.LeaseKeeper` renews on a daemon thread for the whole call at a period
# derived from the lease, and fails closed -- the first failed renewal stops the keeper and
# is re-raised at the next ownership checkpoint.  ``FilePauseRecordStore`` already exposes
# the ``heartbeat(id, token)`` / ``lease_seconds`` surface the keeper needs.
def _still_owned(keeper: Any) -> None:
    """The ownership checkpoint taken before and after every ownership-sensitive write."""
    if keeper is not None:
        keeper.raise_if_lost()


def _committed(keeper: Any, write: Any, *args: Any, **kwargs: Any) -> Any:
    """One fenced write between two ownership checkpoints, exactly as the executor does.

    The checkpoint *after* the write matters as much as the one before it: a renewal that
    fails while the write is in flight need not rotate the token, so the fence would accept
    the write and the recorded loss would be swallowed.
    """
    _still_owned(keeper)
    result = write(*args, **kwargs)
    _still_owned(keeper)
    return result


@dataclass(frozen=True)
class Takeover:
    """The outcome of exactly one run-scoped claim attempt."""

    run_id: str
    claim_outcome: str
    record: dict[str, Any] | None
    lease_token: str = ""
    observed: bool = False


@dataclass
class ResumeOutcome:
    status: str                       # RESUMED | REFUSED | ALREADY_APPLIED | NO_EFFECT
    code: str | None = None
    detail: str = ""
    record: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    resumed_checkpoint_id: str = ""
    revalidation_codes: tuple[str, ...] = ()
    effect_performed: bool = False
    #: The NEXT pause generation, when the resumed run paused again inside this same
    #: re-entry.  ``None`` for a resume that ran to a terminal.
    next_pause_record: dict[str, Any] | None = None


@dataclass
class DisposeOutcome:
    status: str                       # CANCELLED | ABANDONED | ALREADY_DISPOSED | REFUSED
    code: str | None = None
    detail: str = ""
    record: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    residual_terminals: list[dict[str, Any]] = field(default_factory=list)
    ac1_discharged: bool = True
    effect_performed: bool = False


# ---- the commit point of a pause (C1) ------------------------------------------------
def build_pause_record(state: Mapping[str, Any], checkpoint_id: str,
                       checkpoint_digest: str, store_path: str) -> dict[str, Any]:
    """Every field is a pure function of the checkpoint, so a reindex is a byte-identical
    no-op (C4)."""
    binding = state["pause_binding"] or {}
    rows = [dict(row) for row in binding.get("settlement_ledger") or ()]
    now = _now()
    return {
        "schema_version": pause_store.PAUSE_RECORD_SCHEMA_VERSION,
        "run_id": state["run_id"],
        "workflow_id": state["workflow_id"],
        "pause_record_id": binding["pause_record_id"],
        "status": "WAITING_FOR_INPUT",
        "created_at": binding.get("paused_at") or now,
        "updated_at": binding.get("paused_at") or now,
        "owner_id": "", "lease_token": "",
        "lease_expires_at": 0.0, "last_heartbeat_at": 0.0,
        "checkpoint_store_path": str(store_path),
        "thread_id": state["thread_id"], "checkpoint_ns": "",
        "checkpoint_id": checkpoint_id, "checkpoint_digest": checkpoint_digest,
        "disposition": None,
        "ac1_discharged": pause_policy.ac1_discharged(rows),
        "residual_terminals": [],
        "applied": {},
        "projection": pause_policy.project_pause(state),
    }


def finalize_pause(final_state: Mapping[str, Any], *, saver: Any, store: Any,
                   checkpoint_store_path: str | os.PathLike[str],
                   artifact_base: str | os.PathLike[str] | None = None) -> dict[str, Any] | None:
    """Write the Tier-2 record AFTER ``invoke`` returned, referencing the committed head.

    The PAUSE node cannot do this: LangGraph commits the checkpoint after the node returns,
    so the node does not yet know its own ``checkpoint_id``.  Writing here is what makes C1
    -- "a pause record can never name a checkpoint that does not exist" -- true by
    construction rather than by discipline.
    """
    if final_state.get("run_lifecycle") != "WAITING_FOR_INPUT":
        return None
    thread_id = final_state["thread_id"]
    head = saver.head(thread_id)
    if head is None:
        raise PauseRefused("PAUSE_CHECKPOINT_MISSING",
                           f"{final_state['run_id']}: no committed checkpoint on {thread_id}")
    record = build_pause_record(final_state, head,
                                saver.checkpoint_digest(thread_id, head),
                                str(checkpoint_store_path))
    created = dict(store.create(record))
    if artifact_base is not None:
        run_id = final_state["run_id"]
        binding = final_state["pause_binding"] or {}
        for row in binding.get("settlement_ledger") or ():
            _audit(run_id, artifact_base, run_logging.EVENT_PAUSE_SETTLEMENT,
                   task_id=row.get("task_id", ""), dispatch_id=row.get("dispatch_id", ""),
                   phase=final_state["current_phase"], risk=final_state["risk"],
                   result=row.get("terminal_disposition", ""),
                   detail=(f"settlement={row.get('settlement')} "
                           f"worker_resource={row.get('worker_resource')} "
                           f"process_liveness={row.get('process_liveness')} "
                           f"cleanup_authority={row.get('cleanup_authority')} "
                           f"handle_recovery={row.get('handle_recovery')}"))
        _audit(run_id, artifact_base, run_logging.EVENT_RUN_PAUSED,
               phase=final_state["current_phase"], risk=final_state["risk"],
               round_kind=str(final_state["round_kind"]).lower(),
               decision_state=final_state["decision_state"],
               decision_reason_code=final_state["decision_reason_code"] or "",
               result="WAITING_FOR_INPUT",
               detail=f"request={binding.get('request_id')} checkpoint={head}")
        # The phase/iteration scopes stay OPEN on purpose: the run continues later, and
        # .timing_state.json is what preserves them across the process boundary.
        _timing(run_id, artifact_base, run_logging.EVENT_RUN_PAUSED,
                phase=final_state["current_phase"], risk=final_state["risk"],
                detail=f"request={binding.get('request_id')}")
        _run_status(run_id, artifact_base, "WAITING_FOR_INPUT",
                    reason=f"request={binding.get('request_id')}",
                    risk=final_state["risk"])
    return created


# ---- C1 / C2 / C3 --------------------------------------------------------------------
def resolve_store_path(record: Mapping[str, Any], *,
                       artifact_base: str | os.PathLike[str]) -> Path:
    stored = Path(record["checkpoint_store_path"])
    return stored if stored.is_absolute() else Path(artifact_base) / stored


def open_saver(record: Mapping[str, Any], *,
               artifact_base: str | os.PathLike[str]) -> "FileCheckpointSaver":
    return _checkpoint_store().FileCheckpointSaver(
        resolve_store_path(record, artifact_base=artifact_base))


def assert_c1(record: Mapping[str, Any], saver: Any) -> Any:
    config = {"configurable": {"thread_id": record["thread_id"],
                               "checkpoint_ns": record["checkpoint_ns"],
                               "checkpoint_id": record["checkpoint_id"]}}
    tuple_ = saver.get_tuple(config)
    if tuple_ is None:
        raise PauseRefused(
            "PAUSE_CHECKPOINT_MISSING",
            f"{record['run_id']}: the record names checkpoint {record['checkpoint_id']!r}, "
            "which the store does not hold; nothing is reconstructed from the projection")
    return tuple_


def assert_c2(record: Mapping[str, Any], saver: Any) -> None:
    head = saver.head(record["thread_id"], checkpoint_ns=record["checkpoint_ns"])
    if head != record["checkpoint_id"]:
        raise PauseRefused(
            "STALE_CHECKPOINT_HEAD",
            f"{record['run_id']}: head={head!r} != record={record['checkpoint_id']!r}")
    try:
        digest = saver.checkpoint_digest(record["thread_id"], record["checkpoint_id"],
                                         checkpoint_ns=record["checkpoint_ns"])
    except _checkpoint_store().CheckpointStoreError as exc:
        raise PauseRefused("PAUSE_CHECKPOINT_MISSING", str(exc)) from exc
    if digest != record["checkpoint_digest"]:
        raise PauseRefused(
            "STALE_CHECKPOINT_HEAD",
            f"{record['run_id']}: the checkpoint payload no longer matches its digest")


def assert_c3(state: Mapping[str, Any], record: Mapping[str, Any]) -> None:
    """Neither side wins.  The engine must not repair the checkpoint from the projection."""
    projected = pause_policy.project_pause(state)
    if projected != record["projection"]:
        diff = pause_policy.projection_diff(projected, record["projection"])
        raise PauseRefused(
            "PAUSE_PROJECTION_DIVERGED",
            f"{record['run_id']}: fields differ: {list(diff)}; an explicit human "
            "disposition (re-projection under the claim, or cancel/abandon) is required")


def reconstruct(record: Mapping[str, Any], saver: Any) -> dict[str, Any]:
    """The checkpoint is the ONLY input.  ``record['projection']`` is never read here."""
    tuple_ = assert_c1(record, saver)
    values = restore_closed_state(tuple_.checkpoint.get("channel_values") or {})
    return dict(validate_state(values, expected_thread_id=record["thread_id"]))


def validate_pause_consistency(record: Mapping[str, Any], saver: Any) -> dict[str, Any]:
    """C1, then C2, then C3.  Returns the reconstructed, validated ``WorkflowState``."""
    assert_c1(record, saver)
    assert_c2(record, saver)
    state = reconstruct(record, saver)
    assert_c3(state, record)
    return state


# ---- C5: the resume-continuation boundary --------------------------------------------
# C1-C4 answer "is this pause record and this checkpoint the same fact?".  None of them
# answers the question a CRASHED resume leaves behind: the head has moved off the pause,
# so is that this run's own committed continuation -- which a successor must finish -- or
# a head that has nothing to do with this bundle?  C2 answered "stale, refuse" to both,
# which is right for the second and permanently strands the first.
#
# C5 separates them, and it reads nothing but bytes: the thread's head pointer and the
# parent links the checkpoint store already writes with every ``put``.  No in-memory
# state, no wall clock, no "it has probably finished by now".
CONTINUATION_NOT_STARTED = "NOT_STARTED"
CONTINUATION_COMMITTED = "COMMITTED"


def checkpoint_lineage(saver: Any, thread_id: str, checkpoint_ns: str, head: str, *,
                       limit: int = 100_000) -> tuple[str, ...]:
    """``head`` and every ancestor of it, newest first, from the stored parent links.

    ``limit`` and the ``seen`` set only bound a store whose parent links are cyclic or
    unbounded -- neither is producible by :meth:`FileCheckpointSaver.put`, which writes the
    parent exactly once at creation -- but a walk over durable input terminates here by
    construction rather than by trust.
    """
    lineage: list[str] = []
    seen: set[str] = set()
    cursor: str | None = head
    while cursor and cursor not in seen and len(lineage) < limit:
        seen.add(cursor)
        lineage.append(cursor)
        tuple_ = saver.get_tuple({"configurable": {"thread_id": thread_id,
                                                   "checkpoint_ns": checkpoint_ns,
                                                   "checkpoint_id": cursor}})
        if tuple_ is None:
            break
        cursor = (tuple_.parent_config or {}).get("configurable", {}).get("checkpoint_id")
    return tuple(lineage)


def continuation_evidence(record: Mapping[str, Any], saver: Any) -> str:
    """Which side of the effect boundary the dead process reached.  Durable evidence only.

    * :data:`CONTINUATION_NOT_STARTED` -- the head IS the pause checkpoint this record
      names.  Whatever the applied stage says, the continuation is not committed: no node
      has run since the pause, and re-driving the whole resume is byte-identical.
    * :data:`CONTINUATION_COMMITTED` -- the head has moved AND the pause checkpoint is one
      of its ancestors, so this head is this bundle's own continuation.  How far it got is
      the checkpoint's business and nobody else's: re-entering the graph replays exactly
      the supersteps whose results are not committed, which is none of them for a
      continuation that finished.
    * otherwise ``PAUSE_CONTINUATION_UNRECOVERABLE`` -- the head neither is nor descends
      from this pause.  Continuing it would drive a run this record does not speak for, so
      it fails closed by name instead.
    """
    assert_c1(record, saver)
    head = saver.head(record["thread_id"], checkpoint_ns=record["checkpoint_ns"])
    if head is None:
        raise PauseRefused("PAUSE_CHECKPOINT_MISSING",
                           f"{record['run_id']}: {record['thread_id']} carries no head")
    if head == record["checkpoint_id"]:
        return CONTINUATION_NOT_STARTED
    if record["checkpoint_id"] not in checkpoint_lineage(
            saver, record["thread_id"], record["checkpoint_ns"], head):
        raise PauseRefused(
            "PAUSE_CONTINUATION_UNRECOVERABLE",
            f"{record['run_id']}: head={head!r} does not descend from the pause "
            f"checkpoint {record['checkpoint_id']!r}, so it is not this bundle's "
            "continuation and must not be driven")
    return CONTINUATION_COMMITTED


@dataclass(frozen=True)
class _Recovered:
    """What a successor finished, and what it PROVED while finishing it."""

    record: dict[str, Any]
    final: dict[str, Any]
    head: str
    effect_performed: bool
    code: str


def _recover_continuation(run_id: str, *, record: Mapping[str, Any],
                          entry: Mapping[str, Any], saver: Any, graph_factory: Any,
                          keeper: Any, store: Any, lease_token: str,
                          recursion_limit: int | None,
                          artifact_base: str | os.PathLike[str]) -> _Recovered:
    """Finish a continuation a dead process committed, exactly once, from the head.

    The re-entry (``update_state_command``) is NOT replayed: it is already in the
    checkpoint, which is the authority, and replaying it would rewind a run that has moved
    on.  ``invoke(None, config)`` resumes the thread from its committed head, so LangGraph
    itself decides what work remains -- every superstep, and therefore every effect, whose
    result the checkpoint already holds is not re-run.  That is what keeps the whole run at
    exactly one round of effects across the crash:

    * died BEFORE ``invoke``         -> the head is the ACTIVE re-entry, the pending
                                        supersteps run here, and the head advances.
    * died AFTER ``invoke`` returned -> the head is already terminal (or the next pause),
                                        nothing is pending, no effect is performed and the
                                        head does not move.

    The head pointer before and after the call is the durable evidence of which one
    happened, and it is what the returned code reports.
    """
    thread_id, checkpoint_ns = record["thread_id"], record["checkpoint_ns"]
    graph = graph_factory(saver)
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id,
                                               "checkpoint_ns": checkpoint_ns}}
    if recursion_limit:
        config["recursion_limit"] = recursion_limit
    before = saver.head(thread_id, checkpoint_ns=checkpoint_ns)
    final = dict(graph.invoke(None, config))
    _still_owned(keeper)
    after = saver.head(thread_id, checkpoint_ns=checkpoint_ns) or ""
    performed = after != before
    code = ("PAUSE_CONTINUATION_RECOVERED" if performed
            else "PAUSE_CONTINUATION_ALREADY_COMPLETE")
    _committed(keeper, store.promote_applied, run_id, entry["resume_bundle_id"],
               resumed_at=_now(), resumed_checkpoint_id=after, lease_token=lease_token)
    promoted = dict(_committed(keeper, store.mark_resumed, run_id, lease_token,
                               updated_at=_now()))
    _timing(run_id, artifact_base, run_logging.EVENT_RUN_RESUMED,
            phase=str(final.get("current_phase") or ""),
            risk=str(final.get("risk") or ""),
            detail=f"bundle={entry['resume_bundle_id']} recovery={code}")
    _audit(run_id, artifact_base, run_logging.EVENT_RUN_RESUMED,
           phase=str(final.get("current_phase") or ""),
           risk=str(final.get("risk") or ""),
           round_kind=str(final.get("round_kind") or "").lower(),
           result=str(final.get("terminal_status") or "ACTIVE"),
           detail=(f"bundle={entry['resume_bundle_id']} recovery={code} "
                   f"checkpoint={after} inherited_from={before}"))
    return _Recovered(promoted, final, after, performed, code)


# ---- discovery and takeover ----------------------------------------------------------
def discover(artifact_base: str | os.PathLike[str], *,
             langgraph_available: bool = True) -> tuple[dict[str, Any], ...]:
    """Read-only listing with a verdict per run, taking no claim and performing no effect."""
    listings = []
    for listing in pause_store.discover_paused_runs(artifact_base):
        entry = dict(listing)
        if entry["verdict"]:
            listings.append(entry)
            continue
        if entry["status"] != "WAITING_FOR_INPUT":
            entry["verdict"] = f"RUN_ALREADY_{entry['status']}"
            listings.append(entry)
            continue
        if not langgraph_available:
            # Degraded, and named as such.  A missing runtime is never reported as
            # RESUMABLE and never as "the pause is fine".
            entry["verdict"] = "CHECKPOINT_UNVERIFIED"
            entry["detail"] = "LangGraph is absent; C1/C2 could not be evaluated"
            listings.append(entry)
            continue
        store = pause_store.store_for(entry["run_id"], artifact_base=artifact_base)
        record = store.read(entry["run_id"])
        try:
            saver = open_saver(record, artifact_base=artifact_base)
            assert_c1(record, saver)
            assert_c2(record, saver)
            entry["verdict"] = "RESUMABLE"
        except (PauseRefused, _checkpoint_store().CheckpointStoreError) as exc:
            entry["verdict"] = getattr(exc, "code", "PAUSE_RECORD_CORRUPT")
            entry["detail"] = str(exc)
        listings.append(entry)
    return tuple(listings)


def takeover(run_id: str, *, store: Any, observe_timeout_seconds: float | None = None,
             artifact_base: str | os.PathLike[str] | None = None) -> Takeover:
    """Exactly one claim attempt; a loser observes and performs NO effect at any point.

    Because the claim is taken strictly before any external work, a concurrent resume race
    creates no duplicate Task, Dispatch, artifact or log row.

    ``observe_timeout_seconds`` defaults to ``None``, which means the store's own bounded
    lease-derived window (:func:`pause_store.observe_timeout_for`).  That is what makes the
    single call this docstring promises actually reach the takeover when the incumbent has
    stopped heartbeating: a window shorter than the incumbent's lease -- which the previous
    fixed 30s default was, against a 60s lease -- always ended in
    ``PAUSE_OBSERVATION_TIMEOUT`` before takeover was even legal.  An explicit value is
    still honoured exactly, and its timeout stays a retryable outcome that claims nothing.
    """
    try:
        record = dict(store.claim(run_id))
    except pause_store.PauseClaimHeld:
        if artifact_base is not None:
            _audit(run_id, artifact_base, run_logging.EVENT_PAUSE_TAKEOVER_REFUSED,
                   result="PAUSE_CLAIM_HELD", detail="another Coordinator holds the lease")
        settled = store.observe(run_id, timeout_seconds=observe_timeout_seconds,
                                poll_seconds=pause_store.OBSERVE_POLL_SECONDS)
        if settled is not None:
            return Takeover(run_id, f"ALREADY_{settled['status']}", dict(settled),
                            observed=True)
        # The owner's lease lapsed: ONE takeover attempt, then the ladder above.
        record = dict(store.claim(run_id))
        return Takeover(run_id, record["claim_outcome"], record,
                        lease_token=record["lease_token"], observed=True)
    if artifact_base is not None:
        _audit(run_id, artifact_base, run_logging.EVENT_PAUSE_TAKEOVER,
               result=record["claim_outcome"], detail=f"owner={record['owner_id']}")
    return Takeover(run_id, record["claim_outcome"], record,
                    lease_token=record.get("lease_token", ""))


def reindex(artifact_base: str | os.PathLike[str], run_id: str, thread_id: str,
            checkpoint_store_path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """C4: a checkpoint that carries WAITING_FOR_INPUT but has no record re-derives one.

    The repair direction is checkpoint -> record only.  Every derived field is a pure
    function of the checkpoint, so a second reindex is a byte-identical no-op.  The
    converse -- a record naming no reachable checkpoint -- is C1 and is NOT repairable.
    """
    saver = _checkpoint_store().FileCheckpointSaver(checkpoint_store_path)
    head = saver.head(thread_id)
    if head is None:
        return None
    tuple_ = saver.get_tuple({"configurable": {"thread_id": thread_id,
                                               "checkpoint_ns": "",
                                               "checkpoint_id": head}})
    if tuple_ is None:
        return None
    state = dict(validate_state(
        restore_closed_state(tuple_.checkpoint.get("channel_values") or {}),
        expected_thread_id=thread_id))
    if state.get("run_lifecycle") != "WAITING_FOR_INPUT":
        return None
    record = build_pause_record(state, head, saver.checkpoint_digest(thread_id, head),
                                str(checkpoint_store_path))
    store = pause_store.store_for(run_id, artifact_base=artifact_base)
    existing = store.read(run_id)
    if existing is not None:
        # Preserve the live ownership/lease columns; every derived field is rewritten.
        for key in ("owner_id", "lease_token", "lease_expires_at", "last_heartbeat_at",
                    "status", "disposition", "applied", "residual_terminals",
                    "created_at"):
            record[key] = existing[key]
    return dict(store.replace(record))


# ---- reading the decision (S6.3) -----------------------------------------------------
def read_decision_bundle(approval_port: Any, *, run_id: str, request_id: str,
                         decision_item_ids: list[str]) -> dict[str, str]:
    """Read an effective decision for EVERY item, or refuse with a closed code.

    Requiring every item is deliberate: a partially answered bundle is not a resumable
    decision, and the whole bundle is evaluated before any entry is written and before any
    effect, so nothing downstream ever sees a half-read bundle.
    """
    try:
        shown = approval_port.show(run_id=run_id, request_id=request_id)
    except Exception as exc:  # noqa: BLE001 - the OS-30 lineage errors are all fail-closed
        name = type(exc).__name__
        if name in ("LineageFork", "OrphanDecision"):
            raise PauseRefused("RESPONSE_CONFLICT", f"{run_id}: {exc}") from exc
        raise PauseRefused("RESPONSE_STALE_REVISION", f"{run_id}: {exc}") from exc
    if not shown.get("current"):
        raise PauseRefused("RESPONSE_STALE_REVISION",
                           f"{run_id}: {request_id} is superseded by a newer revision")
    effective = dict(shown.get("effective_decisions") or {})
    statuses = dict(shown.get("item_statuses") or {})
    decisions: dict[str, str] = {}
    for item_id in decision_item_ids:
        if item_id not in effective:
            raise PauseRefused("RESPONSE_STALE_REVISION",
                               f"{run_id}: {item_id} is absent from the current request")
        if statuses.get(item_id) == "cancelled":
            raise PauseRefused("RESPONSE_ITEM_UNRESOLVED",
                               f"{run_id}: {item_id} was cancelled; dispose, never resume")
        decision_id = effective[item_id]
        if not decision_id:
            raise PauseRefused("RESPONSE_NOT_FOUND",
                               f"{run_id}: {item_id} has no effective decision yet")
        decisions[item_id] = decision_id
    return decisions


# ---- resume --------------------------------------------------------------------------
def resume_run(run_id: str, *, artifact_base: str | os.PathLike[str], approval_port: Any,
               graph_factory: Any, current_repository: Mapping[str, Any],
               current_artifact: Mapping[str, Any], current_policy_digest: str,
               store: Any = None, recursion_limit: int | None = None,
               observe_timeout_seconds: float | None = None,
               keeper_factory: Any = None) -> ResumeOutcome:
    """Apply the human decision and re-enter the SAME run exactly once.

    The claimed section is held under a live lease from the claim to the last state
    mutation: the decision read, the C1-C3 revalidation, the checkpoint update and the
    whole of ``graph.invoke()`` run inside a :class:`lease_keeper.LeaseKeeper`, which
    renews on a background thread and fails closed.  Losing ownership at any point stops
    this process immediately -- no further effect, no further state mutation -- and is
    reported as ``PAUSE_CLAIM_LOST``, never as a silent success.

    ``keeper_factory`` replaces the keeper outright and exists so a test can drive renewal
    deterministically; it is called as ``factory(store, run_id, lease_token)`` exactly as
    the executor calls its own.

    A resumed run may pause AGAIN inside this same re-entry.  That next generation is
    finalised here, after the keeper has been retired, and is returned as
    ``next_pause_record``: without it the new pause would exist in the checkpoint and
    nowhere in the Tier-2 index, which is invisible to ``discover``.

    **Recovering a continuation a dead process committed.**  Before anything else, the
    claimed section asks C5 (:func:`continuation_evidence`) whether the head already
    carries this bundle's own continuation.  If it does, the re-entry is NOT replayed --
    it is in the checkpoint, which is the authority -- and the run is driven from the head
    to its terminal or its next pause instead, with :data:`pause_policy.PAUSE_RECOVERY_CODES`
    reporting which side of the effect boundary the dead process reached.  The outcome is
    still ``RESUMED``; ``effect_performed`` is False exactly when the continuation had
    already finished and nothing was left to run.
    """
    store = store or pause_store.store_for(run_id, artifact_base=artifact_base)
    try:
        claimed = takeover(run_id, store=store, artifact_base=artifact_base,
                           observe_timeout_seconds=observe_timeout_seconds)
    except pause_store.PauseObservationTimeout as exc:
        return ResumeOutcome("REFUSED", code="PAUSE_OBSERVATION_TIMEOUT", detail=str(exc))
    except pause_store.PauseClaimHeld as exc:
        return ResumeOutcome("REFUSED", code="PAUSE_CLAIM_HELD", detail=str(exc))
    if claimed.claim_outcome in ("ALREADY_RESUMED", "ALREADY_CANCELLED",
                                 "ALREADY_ABANDONED"):
        code = claimed.claim_outcome.replace("ALREADY_", "RUN_ALREADY_")
        return ResumeOutcome("NO_EFFECT", code=code, record=claimed.record)
    record = claimed.record or {}
    lease_token = claimed.lease_token
    factory = keeper_factory or lease_keeper_factory()
    try:
        # Everything inside this block runs under a lease that is being renewed.  The
        # keeper is stopped and VERIFIED on every exit path (success, refusal, crash), and
        # its ``__exit__`` takes the last ownership checkpoint after the final write, so a
        # renewal that failed while that write was in flight cannot be swallowed.
        with factory(store, run_id, lease_token) as keeper:
            saver = open_saver(record, artifact_base=artifact_base)
            # ---- C5, and it has to come BEFORE C2 -----------------------------------
            # C2 asks "is the head still the pause this record names?", which is exactly
            # the question a crashed continuation answers with "no" -- so asking it first
            # turned every recoverable crash window into a permanent STALE_CHECKPOINT_HEAD
            # refusal that reindex() could not repair either, because the head it would
            # have to repair from is ACTIVE and not WAITING_FOR_INPUT.  The record's own
            # applied set is the index into the evidence: a bundle that is claimed
            # (RECORDED or CONTINUING) but not yet promoted.
            in_flight = pause_policy.in_flight_bundle(record)
            if in_flight is not None and \
                    continuation_evidence(record, saver) == CONTINUATION_COMMITTED:
                recovered = _recover_continuation(
                    run_id, record=record, entry=in_flight, saver=saver,
                    graph_factory=graph_factory, keeper=keeper, store=store,
                    lease_token=lease_token, recursion_limit=recursion_limit,
                    artifact_base=artifact_base)
                record, final = recovered.record, recovered.final
                resumed_head, outcome_code = recovered.head, recovered.code
                effect_performed, revalidation_codes = recovered.effect_performed, ()
            else:
                # Not started: the head is still the pause, so this is an ordinary resume
                # -- and a bundle already RECORDED or CONTINUING is re-driven from the
                # top, which is byte-identical because nothing has moved.
                state = validate_pause_consistency(record, saver)
                binding = state["pause_binding"] or {}
                decisions = read_decision_bundle(
                    approval_port, run_id=run_id, request_id=binding["request_id"],
                    decision_item_ids=list(binding["decision_item_ids"]))
                bundle_id = pause_policy.resume_bundle_id(
                    run_id=run_id, request_id=binding["request_id"],
                    pause_record_id=binding["pause_record_id"],
                    decisions=tuple(sorted(decisions.items())))
                stored = record["applied"].get(bundle_id)
                if stored is not None and stored["stage"] == "RESUMED":
                    return ResumeOutcome(
                        "ALREADY_APPLIED", code="RESPONSE_ALREADY_APPLIED", record=record,
                        resumed_checkpoint_id=stored["resumed_checkpoint_id"])
                reentry = pause_policy.resume_reentry(
                    state, current_repository=current_repository,
                    current_artifact=current_artifact,
                    current_policy_digest=current_policy_digest)
                entry = {"resume_bundle_id": bundle_id,
                         "request_id": binding["request_id"],
                         "items": pause_store.applied_items(decisions),
                         "stage": "RECORDED", "recorded_at": _now(),
                         "resumed_at": "", "resumed_checkpoint_id": ""}
                record = dict(_committed(keeper, store.record_applied, run_id, entry,
                                         lease_token))
                # ---- the durable boundary between "intent" and "continuation" --------
                # Written BEFORE the head moves, never after.  The stage may be ahead of
                # the checkpoint -- harmless, because C5 then reads NOT_STARTED and the
                # whole resume is re-driven byte-identically -- but the checkpoint must
                # never be ahead of the stage, because that is precisely the state
                # nothing could name and nothing could recover.
                record = dict(_committed(keeper, store.begin_continuation, run_id,
                                         bundle_id, lease_token=lease_token))
                # ---- THE single effect, owned by the bundle entry and by nothing else -
                graph = graph_factory(saver)
                config: dict[str, Any] = {
                    "configurable": {"thread_id": record["thread_id"],
                                     "checkpoint_ns": record["checkpoint_ns"]}}
                if recursion_limit:
                    config["recursion_limit"] = recursion_limit
                # ``as_node="VALIDATE"`` is what makes the update a RE-ENTRY rather than a
                # write: the run continues from VALIDATE's outgoing edge, i.e. straight
                # into ROUTE, which is the same pure function over the same predicates it
                # was before the pause.
                _committed(
                    keeper, graph.update_state_command,
                    config, "RESUME_PAUSE", as_node="VALIDATE",
                    run_lifecycle="ACTIVE", pause_binding=None,
                    decision_state="CLEAR", decision_reason_code=None,
                    pending_clarification_id=binding["request_id"],
                    round_kind=reentry.round_kind, current_phase=reentry.current_phase,
                    correction_queue=list(reentry.correction_queue),
                    correction_index=reentry.correction_index,
                    binding_generation=reentry.binding_generation,
                    phase_pass_floor=dict(reentry.phase_pass_floor),
                    repository_binding=dict(current_repository),
                    artifact_binding=dict(current_artifact),
                    route_token=None, terminal_reason=None)
                # The long blocking call.  The keeper renews throughout it; the checkpoint
                # immediately after it is what stops a superseded owner from writing the
                # promotion of an effect a successor has already adopted.
                final = graph.invoke(None, config)
                _still_owned(keeper)
                resumed_head = saver.head(record["thread_id"]) or ""
                record = dict(_committed(keeper, store.promote_applied, run_id, bundle_id,
                                         resumed_at=_now(),
                                         resumed_checkpoint_id=resumed_head,
                                         lease_token=lease_token))
                record = dict(_committed(keeper, store.mark_resumed, run_id, lease_token,
                                         updated_at=_now()))
                _timing(run_id, artifact_base, run_logging.EVENT_RUN_RESUMED,
                        phase=reentry.current_phase, risk=state["risk"],
                        detail=f"bundle={bundle_id}")
                _audit(run_id, artifact_base, run_logging.EVENT_RUN_RESUMED,
                       phase=reentry.current_phase, risk=state["risk"],
                       round_kind=str(reentry.round_kind).lower(),
                       result=str(final.get("terminal_status") or "ACTIVE"),
                       detail=(f"bundle={bundle_id} checkpoint={resumed_head} "
                               f"revalidation="
                               f"{','.join(reentry.revalidation_codes) or 'none'}"))
                outcome_code, effect_performed = None, True
                revalidation_codes = reentry.revalidation_codes
        # ---- the NEXT pause generation, if the resumed run paused again ----------------
        # Deliberately outside the keeper: this generation's record is written with no
        # owner and no lease, exactly like the first pause, so the next Coordinator can
        # claim it -- and writing it under a lease this process is about to drop would
        # leave the run claimed by a process that has finished.  The generation above is
        # already RESUMED on disk, which is what makes the create() below a supersede
        # rather than an overwrite of a live pause.  A RECOVERED continuation reaches this
        # too, and must: the pause a dead process drove the run into is otherwise in the
        # checkpoint and nowhere in the Tier-2 index, which is invisible to ``discover``.
        next_record = finalize_pause(final, saver=saver, store=store,
                                     checkpoint_store_path=record["checkpoint_store_path"],
                                     artifact_base=artifact_base)
        return ResumeOutcome("RESUMED", code=outcome_code, record=record,
                             state=dict(final), resumed_checkpoint_id=resumed_head,
                             revalidation_codes=revalidation_codes,
                             effect_performed=effect_performed,
                             next_pause_record=next_record)
    except LeaseRenewalFailed as exc:
        # Fail closed and STAY closed.  The lease was lost while this process was inside
        # the claimed section, so a successor may already own the run: this process writes
        # nothing further and never re-enters the claim path.  The run itself is not
        # damaged -- the checkpoint is the authority and C4 re-derives the record -- but
        # this Coordinator no longer speaks for it.
        _audit(run_id, artifact_base, run_logging.EVENT_RUN_RESUME_REFUSED,
               result="PAUSE_CLAIM_LOST", detail=str(exc))
        return ResumeOutcome("REFUSED", code="PAUSE_CLAIM_LOST", detail=str(exc),
                             record=record)
    except PauseRefused as exc:
        store.release(run_id, lease_token)
        _audit(run_id, artifact_base, run_logging.EVENT_RUN_RESUME_REFUSED,
               result=exc.code, detail=exc.detail)
        return ResumeOutcome("REFUSED", code=exc.code, detail=exc.detail, record=record)
    except pause_store.PauseClaimLost as exc:
        return ResumeOutcome("REFUSED", code="PAUSE_CLAIM_LOST", detail=str(exc),
                             record=record)


# ---- cancel / abandon ----------------------------------------------------------------
def dispose_run(run_id: str, *, artifact_base: str | os.PathLike[str], kind: str,
                actor_id: str, actor_type: str, submission_id: str, reason: str,
                graph_factory: Any, approval_port: Any = None, store: Any = None,
                settlement_port: Any = None, recursion_limit: int | None = None,
                observe_timeout_seconds: float | None = None,
                keeper_factory: Any = None) -> DisposeOutcome:
    """Explicit human cancel/abandon of a paused run.  Never a timeout, never automatic.

    The claimed section is held under a renewed lease for exactly the same reason
    :func:`resume_run` is: a disposal also revalidates, drives ``graph.invoke()`` and then
    writes the terminal disposition, and a lease that lapses in the middle of that would
    let a second Coordinator claim the same run and drive the same effect.
    """
    if kind not in pause_policy.DISPOSITION_KINDS:
        raise ValueError(f"unknown disposition kind: {kind!r}")
    store = store or pause_store.store_for(run_id, artifact_base=artifact_base)
    try:
        claimed = takeover(run_id, store=store, artifact_base=artifact_base,
                           observe_timeout_seconds=observe_timeout_seconds)
    except pause_store.PauseObservationTimeout as exc:
        return DisposeOutcome("REFUSED", code="PAUSE_OBSERVATION_TIMEOUT", detail=str(exc))
    except pause_store.PauseClaimHeld as exc:
        return DisposeOutcome("REFUSED", code="PAUSE_CLAIM_HELD", detail=str(exc))
    if claimed.claim_outcome in ("ALREADY_CANCELLED", "ALREADY_ABANDONED",
                                 "ALREADY_RESUMED"):
        return DisposeOutcome("ALREADY_DISPOSED",
                              code=claimed.claim_outcome.replace("ALREADY_",
                                                                 "RUN_ALREADY_"),
                              record=claimed.record)
    record = claimed.record or {}
    lease_token = claimed.lease_token
    factory = keeper_factory or lease_keeper_factory()
    try:
        with factory(store, run_id, lease_token) as keeper:
            saver = open_saver(record, artifact_base=artifact_base)
            state = validate_pause_consistency(record, saver)
            binding = deepcopy(state["pause_binding"] or {})
            disposition = {
                "kind": kind,
                "cancellation_id": pause_policy.cancellation_id(
                    run_id=run_id, pause_record_id=binding["pause_record_id"],
                    cancel_submission_id=submission_id, cancel_kind=kind),
                "actor_id": actor_id, "actor_type": actor_type,
                "submission_id": submission_id, "reason": reason, "requested_at": _now(),
            }
            pause_policy.validate_disposition(disposition)
            # X1 -- OS-30 request cancellation, for CANCEL only.  ABANDON does NOT run it:
            # there is no human answer to record, and writing a decision_cancelled event
            # attributed to a decision nobody made is exactly what must not happen.
            if kind == "CANCEL" and approval_port is not None:
                _cancel_clarification(approval_port, run_id, binding, disposition)
            binding["disposition"] = disposition
            graph = graph_factory(saver)
            config: dict[str, Any] = {
                "configurable": {"thread_id": record["thread_id"],
                                 "checkpoint_ns": record["checkpoint_ns"]}}
            if recursion_limit:
                config["recursion_limit"] = recursion_limit
            _committed(keeper, graph.update_state_command,
                       config, "REQUEST_DISPOSITION", as_node="VALIDATE",
                       pause_binding=binding, route_token=None, terminal_reason=None)
            final = dict(graph.invoke(None, config))
            _still_owned(keeper)
            rows = [dict(row) for row in (final.get("pause_binding") or {}).get(
                "settlement_ledger") or ()]
            # The candidate handle is read HERE, never carried through the graph: it stays in
            # process memory and reaches exactly one durable place -- this disposition
            # record's residual enumeration -- and never the checkpoint or the journal.
            residual = [_residual_entry(row, _candidate_handle(settlement_port, row))
                        for row in rows if row["terminal_disposition"] == "residual"]
            discharged = pause_policy.ac1_discharged(rows)
            # Retire, never delete: a disposed run's checkpoint is the audit evidence.
            _committed(keeper, saver.retire_thread, record["thread_id"],
                       reason=disposition["cancellation_id"])
            record = dict(_committed(
                keeper, store.settle_disposition,
                run_id, disposition, lease_token, updated_at=_now(),
                residual_terminals=residual, ac1_discharged=discharged))
            status = "CANCELLED" if kind == "CANCEL" else "ABANDONED"
            reason = _disposition_reason(disposition, residual, discharged)
            _audit(run_id, artifact_base,
                   run_logging.EVENT_RUN_CANCELLED if kind == "CANCEL"
                   else run_logging.EVENT_RUN_ABANDONED,
                   phase=str(final.get("current_phase") or ""), risk=str(final.get("risk") or ""),
                   result=status,
                   detail=f"{reason} residual_terminal_count={len(residual)}")
            # The SECOND run_end: contract-legal, and the last row is the authoritative status.
            _run_status(run_id, artifact_base, status, reason=reason,
                        risk=str(final.get("risk") or ""), close_scopes=True)
            return DisposeOutcome(status,
                                  record=record, state=final, residual_terminals=residual,
                                  ac1_discharged=discharged, effect_performed=True)
    except LeaseRenewalFailed as exc:
        # Same fail-closed rule as resume: ownership was lost inside the claimed section,
        # so this process writes nothing further and never re-enters the claim path.
        _audit(run_id, artifact_base, run_logging.EVENT_RUN_RESUME_REFUSED,
               result="PAUSE_CLAIM_LOST", detail=str(exc))
        return DisposeOutcome("REFUSED", code="PAUSE_CLAIM_LOST", detail=str(exc),
                              record=record)
    except _checkpoint_store().CheckpointThreadRetired as exc:
        return DisposeOutcome("REFUSED", code="CHECKPOINT_STORE_RETIRED", detail=str(exc),
                              record=record)
    except PauseRefused as exc:
        store.release(run_id, lease_token)
        _audit(run_id, artifact_base, run_logging.EVENT_RUN_RESUME_REFUSED,
               result=exc.code, detail=exc.detail)
        return DisposeOutcome("REFUSED", code=exc.code, detail=exc.detail, record=record)


def _cancel_clarification(approval_port: Any, run_id: str, binding: Mapping[str, Any],
                          disposition: Mapping[str, Any]) -> None:
    try:
        from scripts.clarification_protocol import ResponseSubmission
    except ImportError:  # installed Skill layout exposes sibling tools directly
        from clarification_protocol import ResponseSubmission  # type: ignore[no-redef]
    submission = ResponseSubmission(
        submission_id=disposition["submission_id"], actor_id=disposition["actor_id"],
        actor_type=disposition["actor_type"], where_recorded="run_cancel",
        responded_at=disposition["requested_at"], cancel=True)
    approval_port.ingest(run_id=run_id, request_id=binding["request_id"],
                         decision_item_id=None, submission=submission)


def _disposition_reason(disposition: Mapping[str, Any], residual: Sequence[Any],
                        discharged: bool) -> str:
    """The `run_end` reason enumerates every residual terminal, plainly."""
    reason = (f"{disposition['cancellation_id']} actor={disposition['actor_id']} "
              f"reason={disposition['reason']}")
    if discharged and not residual:
        return reason
    names = ", ".join(
        f"{entry['terminal_title'] or '(untitled)'}"
        f"[task={entry['task_id'] or '-'} handle_recovery={entry['handle_recovery']}"
        f"{' candidate=' + entry['candidate_handle'] if entry['candidate_handle'] else ''}]"
        for entry in residual)
    return (f"{reason}; ac1_discharged=false; {len(residual)} residual terminal(s) were "
            f"neither released, nor proven exited, nor transferred, and a human must "
            f"dispose of them: {names}")


def _candidate_handle(settlement_port: Any, row: Mapping[str, Any]) -> str:
    """A title match with no verifier, reported so a human has an address to act on."""
    if settlement_port is None or row.get("handle_recovery") != "listing_candidate":
        return ""
    try:
        found = dict(settlement_port.recover_handle(row["intent_id"]))
    except Exception:  # noqa: BLE001 - the report is best-effort; the abandon completes
        return ""
    candidate = found.get("candidate_handle", "")
    return candidate if isinstance(candidate, str) else ""


def _residual_entry(row: Mapping[str, Any], candidate: str = "") -> dict[str, Any]:
    """What the abandon report and the ``run_end`` reason enumerate for a residual terminal.

    ``candidate_handle`` is carried only for a ``listing_candidate``: publishing an address
    the digest *disproves* is worse than publishing none.
    """
    if row.get("handle_recovery") != "listing_candidate":
        candidate = ""
    return {
        "terminal_title": row.get("terminal_title", ""),
        "terminal_digest": row.get("terminal_digest", ""),
        "terminal_role": row.get("terminal_role", ""),
        "terminal_origin": row.get("terminal_origin", ""),
        "provenance_source": row.get("provenance_source", ""),
        "task_id": row.get("task_id", ""),
        "dispatch_id": row.get("dispatch_id", ""),
        "last_observation": row.get("process_liveness", ""),
        "handle_recovery": row.get("handle_recovery", ""),
        "candidate_handle": candidate,
    }
