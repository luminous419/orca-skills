"""Round 3 MAJOR reproduction: a healthy long-running owner is fenced out of its own work.

Runs the *production* executor node for Coordinator A with an ``adapter.start()`` that blocks
(standing in for a 5-15 minute Claude/Codex dispatch) while Coordinator B claims the same
intent after the 60s lease elapses on an injected clock.  No real time passes.

Two modes:

``python3 repro_round3_major.py``            time jumps 300s with nothing renewing the lease
                                             -- the pre-fix behaviour, and what a run with a
                                             lease keeper that cannot see the injected clock
                                             still looks like.
``python3 repro_round3_major.py --paced``    the keeper beats every lease/3, and each beat
                                             *is* the passage of 20s on the injected clock,
                                             which is the fixed production behaviour: B is
                                             refused and A alone settles.
"""
from __future__ import annotations

import sys
import threading
from copy import deepcopy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.deterministic_workflow import runtime_state as rs
from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES, make_intent
from scripts.deterministic_workflow.executor import execute_intent_node
from scripts.deterministic_workflow.fake_adapter import FakeAdapter, FileExternalWorld
from scripts.deterministic_workflow.state import initial_state


class BlockingAdapter(FakeAdapter):
    """``start()`` blocks until the harness releases it, like a long external dispatch."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started = threading.Event()
        self.release = threading.Event()

    def start(self, intent, *, lease_token=None):
        self.started.set()
        self.release.wait(30)
        return super().start(intent, lease_token=lease_token)


class PacedWaiter:
    """A keeper waiter whose every beat advances the injected lease clock by one interval."""

    def __init__(self, clock, advance_by):
        self._clock = clock
        self._advance_by = float(advance_by)
        self._condition = threading.Condition()
        self._pending = 0
        self._cancelled = False

    def __call__(self, stop, interval):
        with self._condition:
            self._condition.wait_for(lambda: self._pending > 0 or self._cancelled, timeout=30)
            if self._cancelled or stop.is_set():
                return True
            self._pending -= 1
        self._clock.advance(self._advance_by)
        return False

    def cancel(self):
        with self._condition:
            self._cancelled = True
            self._condition.notify_all()

    def beat(self, keeper):
        before = keeper.beats
        with self._condition:
            self._pending += 1
            self._condition.notify_all()
        keeper.wait_for_beats(before + 1, timeout=30)


class CapturingFactory:
    def __init__(self, inner):
        self._inner = inner
        self.keeper = None
        self.ready = threading.Event()

    def __call__(self, runtime_state, intent_id, lease_token):
        self.keeper = self._inner(runtime_state, intent_id, lease_token)
        self.ready.set()
        return self.keeper


def main(root: Path, paced: bool = False) -> int:
    root.mkdir(parents=True, exist_ok=True)
    ledger, world_path = root / "ledger.json", root / "world.json"
    for path in (ledger, world_path, Path(f"{ledger}.lock")):
        path.unlink(missing_ok=True)
    clock = rs.ManualLeaseClock()
    store_a = rs.FileRuntimeStateStore(ledger, clock=clock, owner_id="A", lease_seconds=60.0)
    store_b = rs.FileRuntimeStateStore(ledger, clock=clock, owner_id="B", lease_seconds=60.0)
    world = FileExternalWorld(world_path)

    state = initial_state(run_id="run_repro", thread_id="t", phases=("ANALYSIS",),
                          capabilities=BASE_CAPABILITIES)
    intent = make_intent(state, "WORKER", "PHASE_GATE")
    state = {**state, "pending_intent": intent, "intent_status": "PREPARED",
             "pending_role": "WORKER"}
    adapter_a = BlockingAdapter([{"status": "COMPLETE", "unit_test_status": "PASS"}],
                                runtime_state=store_a, external_world=world)

    outcome: dict[str, object] = {}
    node_kwargs: dict[str, object] = {}
    pacer = factory = None
    if paced:
        from scripts.deterministic_workflow.lease_keeper import lease_keeper_factory
        pacer = PacedWaiter(clock, 20.0)
        factory = CapturingFactory(lease_keeper_factory(interval_seconds=20.0, waiter=pacer))
        node_kwargs["keeper_factory"] = factory

    def run_a() -> None:
        try:
            execute_intent_node(adapter_a, runtime_state=store_a, **node_kwargs)(deepcopy(state))
            outcome["a"] = "SETTLED"
        except BaseException as exc:                       # noqa: BLE001 - reporting harness
            outcome["a"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=run_a, name="coordinator-a")
    thread.start()
    adapter_a.started.wait(30)
    print("A claimed the intent and entered adapter.start()")
    token_a = rs.FileRuntimeStateStore(ledger, clock=clock, owner_id="A")._read()[
        intent["intent_id"]]["lease_token"]
    print(f"  A lease_token={token_a[:12]}  lease_expires_at={clock.time() + 60.0}")

    if paced:
        factory.ready.wait(30)
        for _ in range(15):       # 15 beats x 20s = 300s of healthy, renewed work
            pacer.beat(factory.keeper)
        print(f"clock +300s via {factory.keeper.beats} lease renewals (A is healthy)")
    else:
        clock.advance(300.0)      # A is still healthy; 5 minutes of external work
        print("clock +300s (A is healthy and still working, nothing renewing)")
    try:
        record_b = store_b.claim(intent)
        token_b = record_b["lease_token"]
        print(f"B claim -> {record_b['claim_outcome']}   *** B TOOK OVER A HEALTHY OWNER ***")
        print(f"  B lease_token={token_b[:12]} (rotated: {token_b != token_a})")
    except rs.RuntimeStateLeaseHeld as exc:
        print(f"B claim -> REFUSED ({exc})")

    adapter_a.release.set()
    thread.join(30)
    print(f"A finishes its healthy work -> {outcome.get('a')}")
    final = store_a._read()[intent["intent_id"]]
    print(f"ledger owner={final['owner_id']} status={final['status']} "
          f"settlement={'yes' if final['settlement'] else 'no'}")
    return 0


if __name__ == "__main__":
    args = [arg for arg in sys.argv[1:] if arg != "--paced"]
    raise SystemExit(main(Path(args[0]) if args
                          else Path(__file__).resolve().parent / "repro_tmp",
                          paced="--paced" in sys.argv[1:]))
