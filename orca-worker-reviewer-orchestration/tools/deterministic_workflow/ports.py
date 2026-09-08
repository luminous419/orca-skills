"""Runtime-neutral port protocols."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

try:
    from scripts.clarification_protocol import (ClarificationSource, IngestResult, PublishResult,
                                                 ResponseSubmission)
except ImportError:  # installed Skill layout exposes sibling tools directly
    from clarification_protocol import (ClarificationSource, IngestResult, PublishResult,
                                        ResponseSubmission)
from .contracts import ActionIntent, SettlementEvent


@runtime_checkable
class AgentExecutionPort(Protocol):
    def capabilities(self) -> frozenset[str]: ...
    def start(self, intent: ActionIntent, *,
              lease_token: str | None = None) -> Mapping[str, Any]: ...
    def send(self, intent_id: str, command: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def status(self, intent_id: str) -> Mapping[str, Any]: ...
    def interrupt(self, intent_id: str, reason: str) -> Mapping[str, Any]: ...
    def settlement(self, intent_id: str) -> SettlementEvent | None: ...


@runtime_checkable
class ExternalRecoveryPort(Protocol):
    """Optional recovery capabilities an ``AgentExecutionPort`` MAY additionally offer.

    They are optional on purpose.  An adapter declares ``external_lookup`` /
    ``external_resume`` in :meth:`AgentExecutionPort.capabilities` only when the underlying
    runtime really provides them; the executor's recovery ladder refuses to proceed -- rather
    than re-running an effect -- when the capability it needs is absent.  ``lookup`` returns
    ``None`` only to *prove* no effect exists, and raises
    :class:`contracts.ExternalLookupUnavailable` when existence is simply unknown.
    """

    def lookup(self, intent: ActionIntent) -> Mapping[str, Any] | None: ...
    def resume(self, intent: ActionIntent,
               receipt: Mapping[str, Any]) -> SettlementEvent | None: ...


@runtime_checkable
class ArtifactStorePort(Protocol):
    def put(self, intent: ActionIntent, content: bytes) -> Mapping[str, Any]: ...
    def get(self, artifact_id: str) -> bytes: ...
    def evidence(self, evidence_id: str) -> bytes: ...


@runtime_checkable
class RuntimeStatePort(Protocol):
    """Durable claim/receipt/settlement ledger keyed by stable intent identity.

    ``claim`` is written *before* the external effect is attempted so that a restart can
    distinguish "never started" from "may already exist" without re-running the effect.  It
    is also the ownership boundary: exactly one Coordinator may hold a live lease on a stable
    intent, and every other one is refused and must observe instead.  A record therefore
    carries ``owner_id``, ``lease_token``, ``lease_expires_at`` and ``last_heartbeat_at``,
    and ``observe`` always takes an explicit, finite timeout.

    The stored record is a *closed* contract, validated on every read (see
    ``runtime_state.validate_record``): a receipt carries only durable external identifiers
    from ``runtime_state.RECEIPT_KEYS`` and must name at least one of
    ``RECEIPT_IDENTITY_KEYS`` once the effect exists, and a settlement carries exactly the
    canonical ``SettlementEvent`` vocabulary.  ``claim`` additionally re-checks the whole
    stored identity (``runtime_state.IDENTITY_KEYS``) against the intent presenting itself,
    because a record that is internally coherent may still belong to another intent.

    The lease token is a *fence*: ``record_receipt``, ``settle`` and ``heartbeat`` all
    require the token ``claim`` returned and reject a stale or missing one, so an executor
    whose lease was taken over cannot write its external identity into the successor's
    record.  ``AgentExecutionPort.start`` therefore accepts that token and carries it to
    whatever writes the receipt.
    """

    def get_receipt(self, intent_id: str) -> Mapping[str, Any] | None: ...
    def get_settlement(self, intent_id: str) -> SettlementEvent | None: ...
    def claim(self, intent: ActionIntent) -> Mapping[str, Any]: ...
    def heartbeat(self, intent_id: str, lease_token: str) -> Mapping[str, Any]: ...
    def release(self, intent_id: str, lease_token: str) -> None: ...
    def observe(self, intent_id: str, *, timeout_seconds: float,
                poll_seconds: float) -> Mapping[str, Any] | None: ...
    def record_receipt(self, intent_id: str, receipt: Mapping[str, Any],
                       lease_token: str) -> Mapping[str, Any]: ...
    def settle(self, intent_id: str, event: SettlementEvent,
               lease_token: str) -> Mapping[str, Any]: ...


@runtime_checkable
class RunPauseStatePort(Protocol):
    """Run-scoped durable pause index, coordination fence and projection.

    Deliberately separate from :class:`RuntimeStatePort`, which is keyed on ``intent_id``
    and cannot answer a run-scoped question.  It is NEVER the authority for execution
    state; that is the OS-40 checkpoint (PLAN D2/F-001).  ``projection`` is a subordinate
    view of the checkpoint, kept so a discovery sweep can explain a paused run without
    opening the Tier-1 store, and cross-checked (C3) rather than trusted.

    Every mutating call is fenced by the ``lease_token`` :meth:`claim` minted.  There is
    deliberately no "no token supplied" branch: an absent token is a missing capability,
    not permission to skip the check.
    """

    def read(self, run_id: str) -> Mapping[str, Any] | None: ...
    def create(self, record: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def claim(self, run_id: str) -> Mapping[str, Any]: ...
    def heartbeat(self, run_id: str, lease_token: str) -> Mapping[str, Any]: ...
    def release(self, run_id: str, lease_token: str) -> None: ...
    def observe(self, run_id: str, *, timeout_seconds: float,
                poll_seconds: float) -> Mapping[str, Any] | None: ...
    def update_pointer(self, run_id: str, *, checkpoint_id: str, checkpoint_digest: str,
                       projection: Mapping[str, Any],
                       lease_token: str) -> Mapping[str, Any]: ...
    def record_applied(self, run_id: str, entry: Mapping[str, Any],
                       lease_token: str) -> Mapping[str, Any]:
        """Write the ONE bundle-level applied entry, atomically.

        ``entry`` is a whole applied bundle covering every decision item of the request --
        never one item.  One call, one whole-record write under the store's critical
        section, so there is no window in which a subset of a bundle's items is recorded
        and the rest is not.  Refuses with ``PAUSE_LIFECYCLE_INCOHERENT`` when the items
        are not exactly the record's ``decision_item_ids``, and with ``RESPONSE_CONFLICT``
        when a *different* bundle is already applied.
        """
    def mark_resumed(self, run_id: str, lease_token: str) -> Mapping[str, Any]: ...
    def settle_disposition(self, run_id: str, disposition: Mapping[str, Any],
                           lease_token: str) -> Mapping[str, Any]: ...


@runtime_checkable
class LifecycleSettlementPort(Protocol):
    """Settle a dispatch and account terminal ownership, for pause and disposal.

    ``AgentExecutionPort.interrupt`` cannot express this: interrupting is not settling, and
    it says nothing about the four axes.  Declared by an adapter as the capability
    ``lifecycle_settlement``; an adapter that cannot honour it must not declare it, and
    pause then correctly falls back to BLOCK.
    """

    def open_dispatches(self) -> tuple[str, ...]:
        """Every dispatch of THIS run that is not yet finished, reconstructed durably.

        Must be answerable by a process that holds none of the objects of the process that
        created the dispatches.  An implementation that can only read its own memory does
        not satisfy this method and must not declare the capability.  A source that cannot
        be read is "unknown", never "empty": raise rather than return a short tuple.
        """

    def recover_handle(self, intent_id: str) -> Mapping[str, Any]:
        """Resolve this row's live terminal handle, or say why it cannot.

        Returns ``{"handle": str | None, "handle_recovery": <closed vocabulary member>}``.
        Read-only: enumerates and verifies, mutates nothing, and is safe to repeat.  A
        handle is returned ONLY for ``listing_verified`` -- i.e. only when a durable digest
        proved it.  ``listing_candidate`` reports a title match with no verifier and MUST
        NOT be acted on; it exists so an abandon report can name the terminal.  A source
        that cannot be read raises rather than returning ``not_listed``.
        """

    def account_dispatch(self, intent_id: str) -> Mapping[str, Any]: ...
    def recover_dispatch(self, intent_id: str, *, reason: str) -> Mapping[str, Any]: ...
    def release_terminal(self, intent_id: str, *, authority: str) -> Mapping[str, Any]: ...


@runtime_checkable
class HumanApprovalPort(Protocol):
    def publish(self, *, run_id: str, sources: Sequence[ClarificationSource]) -> PublishResult: ...
    def show(self, *, run_id: str, request_id: str) -> Mapping[str, object]: ...
    def ingest(self, *, run_id: str, request_id: str, decision_item_id: str | None,
               submission: ResponseSubmission) -> IngestResult: ...


class ClockPort(Protocol):
    def now(self) -> str: ...


class LeaseClockPort(Protocol):
    """Injectable seconds-resolution clock for lease expiry and bounded waits.

    Injecting it is what lets every lease/observation/lock-timeout test advance time
    explicitly instead of sleeping, so none of them is timing-flaky.
    """

    def time(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class IdGeneratorPort(Protocol):
    def stable_id(self, namespace: str, canonical_payload: bytes) -> str: ...


# ---- OS-43: the Watchdog's five ports (additive; nothing above is touched) -------------
# They follow this file's existing conventions exactly, including the durability rule
# stated at ``LifecycleSettlementPort.open_dispatches``: every one of them must be
# answerable by a process that holds none of the objects of the process that created the
# state, and a source that cannot be read is "unknown", never "empty".
@runtime_checkable
class RunDiscoveryPort(Protocol):
    """Every run under one artifact base that a supervisor may have to consider.

    Symmetric with ``pause_store.discover_paused_runs`` and deliberately wider: that
    function ``continue``s past every directory with no pause record, so a stalled ACTIVE
    run is invisible to it.  This port reaches those runs too.  Read-only: it takes no
    claim and performs no effect, the same discipline ``pause_runtime.discover`` states.

    A run root that cannot be read is reported as a listing whose ``verdict`` names the
    defect -- never omitted.  An unreadable RUNS ROOT raises: "unknown" is not "empty".
    """

    def discover(self) -> tuple[Mapping[str, Any], ...]: ...


@runtime_checkable
class RunObservationPort(Protocol):
    """One atomic read of every durable authority a fact vector needs, for ONE run.

    ``turn_boundary.observe`` already has exactly this shape -- everything the verdict
    needs, gathered in one atomic invocation -- and the classifier's determinism argument
    rests on the vector being ONE snapshot with no re-read mid-classification.

    Every method RAISES when its authority exists but cannot be read, and reports a
    legitimately ABSENT authority as an absence.  The port never collapses the two:
    turning a refusal into the F1 fact is the snapshot builder's job, so no implementation
    of this port ever has to lie.  ``durable_wait`` is TRI-VALUED for this reason and must
    not reuse ``turn_boundary.observe_durable_wait``, whose three witnesses each fail
    OPEN -- safe for a turn end, which only refuses a turn, and unsafe here, where it
    would auto-resume a human wait.
    """

    def orca_state(self, run_id: str) -> Mapping[str, Any]: ...
    def checkpoint_state(self, run_id: str) -> Mapping[str, Any]: ...
    def pause_state(self, run_id: str) -> Mapping[str, Any] | None: ...
    def durable_wait(self, run_id: str) -> Mapping[str, Any]: ...
    def delivery_obligations(self, run_id: str) -> tuple[str, ...]: ...
    def foreign_lease(self, run_id: str) -> Mapping[str, Any] | None: ...
    def declared_capabilities(self, run_id: str) -> frozenset[str]: ...


@runtime_checkable
class CoordinatorLivenessPort(Protocol):
    """The run-scoped Coordinator liveness lease, READ ONLY.

    ``status`` is deliberately four-valued and never boolean.  ABSENT is not EXPIRED: a
    run whose Coordinator never published a lease has no liveness evidence at all, and
    reading that as "expired" would reinstate the false positive the record exists to
    remove.  UNREADABLE is not EXPIRED either; it is routed to F1.
    """

    def status(self, run_id: str) -> str: ...
    def record(self, run_id: str) -> Mapping[str, Any] | None: ...


#: The named, fail-closed reason a recovery could not even be REQUESTED for one run.
#: It is deliberately NOT a member of the engine's closed outcome vocabulary: nothing was
#: claimed, nothing was invoked and no effect was attempted, so reporting it as an outcome
#: would be reporting work that never started.
RECOVERY_GRAPH_UNAVAILABLE = "RECOVERY_GRAPH_UNAVAILABLE"


class RecoveryPreconditionUnavailable(Exception):
    """A port cannot supply, FOR THIS RUN, something a recovery needs before it begins.

    Raised by :meth:`RecoveryInvocationPort.build_request` (and by the engine as a second
    line of defence) instead of handing on a graph factory that would produce ``None`` and
    fail with an ``AttributeError`` mid-invocation.  The distinction is the whole point:
    an ``AttributeError`` caught by a generic handler is indistinguishable from a run that
    was acted upon and failed, whereas this is a NAMED refusal that a supervisor must
    report and must not count as an action.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@runtime_checkable
class RecoveryInvocationPort(Protocol):
    """The ONLY action the supervisor core can take, and it is the ENGINE's action.

    The core holds no other route into the engine: it does not import ``routing``,
    ``graph``, ``executor``, ``pause_runtime.resume_run``, ``pause_store.claim`` or
    ``runtime_state.claim``, so recoverability, the next node, the claim, the fence and
    the resume semantics are unreachable from it by construction rather than by
    convention.

    ``recover`` takes ONE frozen request whose field set is closed and contains no
    verdict, next node, phase, decision-bundle id, lease token or force flag, and returns
    ONE outcome from a closed set.  A refusal outcome carries no continuation handle -- no
    token, no saver, no state, no graph -- so there is nothing on it a caller could use to
    proceed anyway.
    """

    def recover(self, request: Any) -> Any: ...

    def identity(self, *, run_id: str, thread_id: str, checkpoint_ns: str,
                 head_checkpoint_id: str, recovery_kind: str) -> str:
        """The ENGINE's own idempotency identity for one attempt.  Pure, and not an action.

        It lives on this port rather than in the supervisor core for the same reason
        ``recover`` does: the identity is the engine's, and two spellings of it would let
        the budget and the attempt entry key on different things.
        """

    def build_request(self, *, run_id: str, recovery_kind: str) -> Any:
        """Assemble the closed request from observations the supervisor supplies.

        Here rather than in the core for the same reason: the core must not be able to
        construct an engine type at all.  There is no parameter through which a caller
        could express a next node, a verdict, a phase, a decision-bundle id, a lease token
        or a force flag -- ``run_id`` and ``recovery_kind`` are both observations.

        The request is assembled FOR ``run_id``, so everything run-specific on it -- the
        graph the engine would resume above all -- is resolved here, per run, and never
        once for a whole sweep.  An implementation that cannot resolve it for this run
        raises :class:`RecoveryPreconditionUnavailable` rather than returning a request
        that would fail inside the engine.
        """


@runtime_checkable
class SupervisorAuditPort(Protocol):
    """The append-only watchdog ledger: the supervisor's SOLE durable memory.

    ``append`` publishes ONE immutable, sequence-allocated record and refuses an unknown
    event before publishing, exactly as ``append_coordinator_audit_record`` does.
    ``fold`` rebuilds the retry budget, backoff deadline and escalation state a previous
    process left behind, and RAISES rather than returning a truncated history --
    ``replay_delivery_ledger``'s rule, for the same reason: a truncated history is
    indistinguishable from "nothing has been attempted", which is how a bounded retry
    becomes an unbounded one.

    An ABSENT ledger is not damage and folds to an empty state.  Unlike OS-31's ``_audit``
    helper, which swallows every exception because there "audit is evidence, never a
    gate", ``append`` IS a gate here: it is the budget's only source, so a record that
    cannot be published means the attempt it precedes must not be made.
    """

    def append(self, run_id: str, event: str,
               record: Mapping[str, Any]) -> tuple[str, int]: ...
    def fold(self, run_id: str) -> Mapping[str, Mapping[str, Any]]: ...
