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
