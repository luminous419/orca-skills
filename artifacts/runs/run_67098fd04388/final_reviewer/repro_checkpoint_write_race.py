"""Deterministic adversarial interleaving: renewal fails after the final checkpoint."""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from scripts.deterministic_workflow import runtime_state as rs
from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES, make_intent
from scripts.deterministic_workflow.executor import execute_intent_node
from scripts.deterministic_workflow.fake_adapter import FakeAdapter
from scripts.deterministic_workflow.lease_keeper import lease_keeper_factory
from scripts.deterministic_workflow.state import initial_state


class OneBeat:
    def __init__(self):
        self.go = threading.Event()

    def __call__(self, stop, interval):
        self.go.wait(5)
        return stop.is_set()

    def cancel(self):
        self.go.set()


class InterleavingLedger:
    """Park settle after executor's checkpoint; fail heartbeat in that exact gap."""
    def __init__(self, inner):
        self.inner = inner
        self.lease_seconds = inner.lease_seconds
        self.settle_entered = threading.Event()
        self.allow_settle = threading.Event()
        self.heartbeat_failed = threading.Event()

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def heartbeat(self, intent_id, lease_token):
        self.heartbeat_failed.set()
        raise rs.RuntimeStateLockTimeout("injected renewal failure")

    def settle(self, intent_id, event, lease_token):
        self.settle_entered.set()
        if not self.allow_settle.wait(5):
            raise AssertionError("settle was not released")
        return self.inner.settle(intent_id, event, lease_token)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        inner = rs.FileRuntimeStateStore(Path(tmp) / "ledger.json", owner_id="A")
        ledger = InterleavingLedger(inner)
        state = dict(initial_state(run_id="run_race", thread_id="t", phases=("ANALYSIS",),
                                   capabilities=BASE_CAPABILITIES))
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        state.update(pending_intent=intent, intent_status="PREPARED", pending_role="WORKER")
        adapter = FakeAdapter([{"status": "COMPLETE", "unit_test_status": "PASS"}])
        pacer = OneBeat()
        node = execute_intent_node(adapter, runtime_state=ledger,
                                   keeper_factory=lease_keeper_factory(waiter=pacer))
        outcome = {}

        def run():
            try:
                outcome["state"] = node(state)
            except BaseException as exc:
                outcome["error"] = exc

        thread = threading.Thread(target=run)
        thread.start()
        assert ledger.settle_entered.wait(5), "executor did not reach final write"
        pacer.go.set()
        assert ledger.heartbeat_failed.wait(5), "renewal did not fail"
        ledger.allow_settle.set()
        thread.join(5)
        record = inner._read()[intent["intent_id"]]
        print(f"thread_alive={thread.is_alive()}")
        print(f"executor_error={outcome.get('error')!r}")
        print(f"executor_returned={outcome.get('state') is not None}")
        print(f"ledger_status={record['status']}")
        print(f"settlement_written={record['settlement'] is not None}")
        print(f"effect_count={adapter.effect_count}")


if __name__ == "__main__":
    main()
