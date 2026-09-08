"""Shared OS-43 fixtures: fact-vector builders and the fake observation ports.

Named ``test_...`` so unittest discovery can import it from the suites that share it; it
deliberately declares no ``TestCase`` of its own, exactly as
``test_deterministic_workflow_pause_fixture`` does.

Everything here is deterministic and offline.  There is no clock but
``runtime_state.ManualLeaseClock``, no adapter but ``FakeAdapter``, and nothing sleeps.
"""
from __future__ import annotations

from typing import Any

from scripts.deterministic_workflow import watchdog_observation as observation_module
from scripts.deterministic_workflow.watchdog_observation import (SUPPORT_ABSENT_DECLARED,
                                                                 SUPPORT_SUPPORTED,
                                                                 SUPPORT_UNSUPPORTED,
                                                                 ObservationSnapshot)

FACT_IDS = observation_module.FACT_IDS
SAFETY_RELEVANT_FACTS = observation_module.SAFETY_RELEVANT_FACTS


def make_snapshot(*, run_id: str = "run_w", true_facts: tuple[str, ...] = (),
                  support: dict[str, str] | None = None, pause_verdict: str = "",
                  next_node: str = "", head: str = "cp_1",
                  status_authority: str = "workflow_checkpoint",
                  liveness_status: str = "EXPIRED") -> ObservationSnapshot:
    """A fact vector built by NAME, so a witness reads as the design states it.

    Every fact not named true is false, and every fact's support defaults to
    ``SUPPORTED`` -- i.e. COVERED -- so a test that wants an uncovered fact has to say so
    explicitly.  That is what keeps ``UNSUPPORTED`` from leaking into a witness by
    accident.
    """
    facts = {fact: fact in true_facts for fact in FACT_IDS}
    vector = {fact: SUPPORT_SUPPORTED for fact in FACT_IDS}
    vector.update(support or {})
    return ObservationSnapshot(
        run_id=run_id, observed_at="2026-09-08T00:00:00Z", facts=facts, support=vector,
        evidence={fact: () for fact in FACT_IDS}, pause_verdict=pause_verdict,
        next_node=next_node, thread_id="t", checkpoint_ns="", head_checkpoint_id=head,
        status_authority=status_authority, liveness_status=liveness_status,
        snapshot_digest="digest")


# ---- the four UR-3 / CON-4 witnesses, by name -----------------------------------------
def witness_w1() -> ObservationSnapshot:
    """W1 -- ``F6 ∧ ¬F5`` on a ``declared_only`` run.  The M1 (F-006) witness.

    No OS-40 checkpoint store, so ``status_authority == "declared_only"``: the checkpoint
    contributor of F2 and F5 is withdrawn and each of those facts KEEPS another declared
    contributor, so neither becomes uncovered.  One ``dispatched`` Task with a live worker
    row (F6 true) and no unblocked undispatched Task (F5 false).  No member of
    ``SAFETY_RELEVANT_FACTS`` is ``UNSUPPORTED``, so F11 is false and R2 declines.
    """
    return make_snapshot(
        true_facts=("F6",),
        support={"F2": SUPPORT_ABSENT_DECLARED, "F3": SUPPORT_ABSENT_DECLARED,
                 "F4": SUPPORT_ABSENT_DECLARED, "F9": SUPPORT_ABSENT_DECLARED},
        status_authority="declared_only", head="")


def witness_w2() -> ObservationSnapshot:
    """W2 -- ``F3 ∧ F5``.  The M2 witness: a human wait AND a runnable next node."""
    return make_snapshot(true_facts=("F3", "F5"), next_node="PREPARE_WORKER")


def witness_w3() -> ObservationSnapshot:
    """W3 -- ``F7 ∧ F5``.  The M3 witness: reconciliation owed AND a runnable node."""
    return make_snapshot(true_facts=("F7", "F5"), next_node="PREPARE_WORKER")


def witness_w4(uncovered: str) -> ObservationSnapshot:
    """W4 -- the CON-4 witness: the R11 shape with ONE safety-relevant fact UNCOVERED.

    F5 is ``SUPPORTED`` and true -- the shape that would otherwise reach R11 -- while
    ``uncovered`` has no declared authority at all and every other fact is covered.
    """
    if uncovered == "F5":
        return make_snapshot(true_facts=(), support={"F5": SUPPORT_UNSUPPORTED})
    return make_snapshot(true_facts=("F5",), next_node="PREPARE_WORKER",
                         support={uncovered: SUPPORT_UNSUPPORTED})


# ---- fake observation ports ------------------------------------------------------------
class FakeObservationPort:
    """A ``RunObservationPort`` whose every answer is scripted by the test.

    ``RAISE_UNAVAILABLE`` and ``RAISE_UNSUPPORTED`` are the two sentinels that let a test
    say "this authority exists and raised" and "nothing covers this fact" respectively --
    the distinction the whole CON-4 correction turns on.
    """

    RAISE_UNAVAILABLE = object()
    RAISE_UNSUPPORTED = object()

    def __init__(self, **answers: Any) -> None:
        self.answers = {
            "orca_state": {"active_dispatches": (), "runnable_actions": ()},
            "checkpoint_state": {"present": False, "run_status": "", "next_node": "",
                                 "thread_id": "", "checkpoint_ns": "",
                                 "head_checkpoint_id": "", "status_authority": ""},
            "pause_state": None,
            "durable_wait": {"evidence": (), "unreadable": ()},
            "delivery_obligations": (),
            "foreign_lease": None,
            "declared_capabilities": frozenset({"external_resume"}),
        }
        self.answers.update(answers)
        self.calls: list[str] = []

    def _answer(self, name: str, run_id: str) -> Any:
        self.calls.append(name)
        value = self.answers[name]
        if value is self.RAISE_UNAVAILABLE:
            raise observation_module.ObservationUnavailable(f"{run_id}: {name}")
        if value is self.RAISE_UNSUPPORTED:
            raise observation_module.ObservationUnsupported(f"{run_id}: {name}")
        return value

    def orca_state(self, run_id: str) -> Any:
        return self._answer("orca_state", run_id)

    def checkpoint_state(self, run_id: str) -> Any:
        return self._answer("checkpoint_state", run_id)

    def pause_state(self, run_id: str) -> Any:
        return self._answer("pause_state", run_id)

    def durable_wait(self, run_id: str) -> Any:
        return self._answer("durable_wait", run_id)

    def delivery_obligations(self, run_id: str) -> Any:
        return self._answer("delivery_obligations", run_id)

    def foreign_lease(self, run_id: str) -> Any:
        return self._answer("foreign_lease", run_id)

    def declared_capabilities(self, run_id: str) -> Any:
        return self._answer("declared_capabilities", run_id)


class FakeLivenessPort:
    """A ``CoordinatorLivenessPort`` returning one scripted four-valued answer."""

    def __init__(self, status: str = "EXPIRED", *, raises: bool = False) -> None:
        self._status = status
        self._raises = raises

    def status(self, run_id: str) -> str:
        if self._raises:
            raise observation_module.ObservationUnavailable(f"{run_id}: liveness")
        return self._status

    def record(self, run_id: str) -> Any:
        return None


class FakeDiscoveryPort:
    def __init__(self, *run_ids: str) -> None:
        self.run_ids = run_ids

    def discover(self) -> tuple[dict[str, Any], ...]:
        return tuple({"run_id": run_id, "verdict": "STALLED_RECOVERABLE"}
                     for run_id in self.run_ids)


class RecordingAudit:
    """A ``SupervisorAuditPort`` over a dict, so a test can read what was recorded."""

    def __init__(self, *, fold_raises: bool = False,
                 rows: dict[str, dict[str, Any]] | None = None,
                 append_raises_on: str = "") -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []
        self._fold_raises = fold_raises
        self._rows = rows or {}
        self._append_raises_on = append_raises_on

    def append(self, run_id: str, event: str, record: dict[str, Any]) -> tuple[str, int]:
        if self._append_raises_on and event == self._append_raises_on:
            from scripts.deterministic_workflow.watchdog_audit import WatchdogAuditError
            raise WatchdogAuditError(f"{run_id}: {event} could not be published")
        self.records.append((run_id, event, dict(record)))
        return (f"{run_id}/{event}", len(self.records) - 1)

    def fold(self, run_id: str) -> dict[str, dict[str, Any]]:
        if self._fold_raises:
            from scripts.deterministic_workflow.watchdog_audit import WatchdogAuditError
            raise WatchdogAuditError(f"{run_id}: the ledger could not be folded")
        return {key: dict(value) for key, value in self._rows.items()}

    def events(self) -> list[str]:
        return [event for _run, event, _record in self.records]


class ScriptedRecovery:
    """A ``RecoveryInvocationPort`` returning scripted outcomes and counting invocations."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[Any] = []

    def identity(self, *, run_id: str, thread_id: str, checkpoint_ns: str,
                 head_checkpoint_id: str, recovery_kind: str) -> str:
        from scripts.deterministic_workflow.recovery_runtime import recovery_identity
        return recovery_identity(run_id=run_id, thread_id=thread_id,
                                 checkpoint_ns=checkpoint_ns,
                                 head_checkpoint_id=head_checkpoint_id,
                                 recovery_kind=recovery_kind)

    def build_request(self, *, run_id: str, recovery_kind: str) -> dict[str, Any]:
        return {"run_id": run_id, "recovery_kind": recovery_kind}

    def recover(self, request: Any) -> Any:
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError("recover() was called more times than the test scripted")
        return self.outcomes.pop(0)


def outcome(status: str, code: str, **kwargs: Any) -> Any:
    from scripts.deterministic_workflow.recovery_runtime import RecoveryOutcome
    return RecoveryOutcome(status, code, recovery_id=kwargs.pop("recovery_id", "rid"),
                           **kwargs)
