"""Reviewer-only deterministic checks for the accepted-write and expiry boundaries."""
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


class Pacer:
    def __init__(self):
        self.go = threading.Event()

    def __call__(self, stop, interval):
        self.go.wait(5)
        return stop.is_set()

    def cancel(self):
        self.go.set()


class ParkedLedger:
    def __init__(self, inner):
        self.inner = inner
        self.lease_seconds = inner.lease_seconds
        self.entered = threading.Event()
        self.release = threading.Event()
        self.failed = threading.Event()

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def heartbeat(self, intent_id, lease_token):
        self.failed.set()
        raise rs.RuntimeStateLockTimeout("injected renewal failure")

    def settle(self, intent_id, event, lease_token):
        self.entered.set()
        assert self.release.wait(5)
        return self.inner.settle(intent_id, event, lease_token)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        clock = rs.ManualLeaseClock()
        path = Path(tmp) / "ledger.json"
        inner = rs.FileRuntimeStateStore(path, owner_id="A", clock=clock)
        ledger = ParkedLedger(inner)
        state = dict(initial_state(run_id="run_semantics", thread_id="t",
                                   phases=("ANALYSIS",), capabilities=BASE_CAPABILITIES))
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        state.update(pending_intent=intent, intent_status="PREPARED", pending_role="WORKER")
        result = {"status": "COMPLETE", "unit_test_status": "PASS", "marker": "truth"}
        adapter_a = FakeAdapter([result])
        pacer = Pacer()
        node_a = execute_intent_node(adapter_a, runtime_state=ledger,
                                    keeper_factory=lease_keeper_factory(waiter=pacer))
        outcome = {}

        def run_a():
            try:
                outcome["state"] = node_a(state)
            except BaseException as exc:
                outcome["error"] = exc

        thread = threading.Thread(target=run_a)
        thread.start()
        assert ledger.entered.wait(5)
        pacer.go.set()
        assert ledger.failed.wait(5)
        ledger.release.set()
        thread.join(5)
        record = inner._read()[intent["intent_id"]]

        adapter_b = FakeAdapter([{"status": "COMPLETE", "marker": "duplicate"}])
        adopted = execute_intent_node(
            adapter_b, runtime_state=rs.FileRuntimeStateStore(path, owner_id="B", clock=clock)
        )(state)
        print(f"a_failed_closed={outcome.get('state') is None and outcome.get('error') is not None}")
        print(f"stored_marker={record['settlement']['result']['marker']}")
        print(f"b_adopted_same_event={adopted['pending_event'] == record['settlement']}")
        print(f"b_external_effects={adapter_b.effect_count}")

    clock = rs.ManualLeaseClock()
    store = rs.InMemoryRuntimeStateStore(owner_id="A", clock=clock, lease_seconds=1.0)
    state = dict(initial_state(run_id="run_expiry", thread_id="t", phases=("ANALYSIS",),
                               capabilities=BASE_CAPABILITIES))
    intent = make_intent(state, "WORKER", "PHASE_GATE")
    claim = store.claim(intent)
    clock.advance(2.0)
    stored = store.record_receipt(intent["intent_id"], {"external_id": "ext-1"},
                                  claim["lease_token"])
    print(f"expired_without_takeover_receipt_status={stored['status']}")


if __name__ == "__main__":
    main()
