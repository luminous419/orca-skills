"""Attempt-2 adversarial stress: many keepers and cross-process corruption."""
from __future__ import annotations

import json
import multiprocessing
import tempfile
import threading
from pathlib import Path

from scripts.deterministic_workflow.lease_keeper import LeaseKeeper
from scripts.deterministic_workflow.runtime_state import (
    FileRuntimeStateStore, ManualLeaseClock, RuntimeStateCorrupt,
)
from scripts.test_deterministic_workflow_lease_keeper import BeatPacer, JOIN_TIMEOUT


def intent(index: int) -> dict:
    return {
        "intent_id": f"intent_stress_{index}", "command_id": f"command_{index}",
        "payload_digest": f"digest_{index}", "run_id": "run_stress", "phase": "BUGFIX",
        "role": "WORKER", "round_kind": "PRIMARY",
    }


def corrupt_under_process_lock(path: str, ready, go) -> None:
    store = FileRuntimeStateStore(path, owner_id="corrupter")
    ready.set()
    go.wait(JOIN_TIMEOUT)
    with store._locked():
        Path(path).write_text('{"schema_version":"os40.runtime_state.v2","records":[]}',
                              encoding="utf-8")


def main() -> None:
    count = 32
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "ledger.json")
        clock = ManualLeaseClock()
        store = FileRuntimeStateStore(path, owner_id="coordinator-A", clock=clock)
        claims = [store.claim(intent(i)) for i in range(count)]
        pacers = [BeatPacer() for _ in range(count)]
        keepers = [LeaseKeeper(store, intent(i)["intent_id"], claims[i]["lease_token"],
                               interval_seconds=20.0, waiter=pacers[i])
                   for i in range(count)]
        for keeper in keepers:
            keeper.start()

        errors: list[str] = []
        threads = [threading.Thread(
            target=lambda i=i: pacers[i].request_beat(keepers[i]), daemon=True)
                   for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(JOIN_TIMEOUT)
            if thread.is_alive():
                errors.append("heartbeat request wedged")

        parsed = json.loads(Path(path).read_text(encoding="utf-8"))
        beat_ok = all(k.beats == 1 and not k.lost for k in keepers)
        records_ok = len(parsed["records"]) == count and all(
            parsed["records"][intent(i)["intent_id"]]["last_heartbeat_at"] == clock.time()
            for i in range(count))
        print(f"many_intents={count} beat_ok={beat_ok} records_ok={records_ok} errors={errors}")
        for keeper in keepers:
            keeper.stop()

        # Fresh keepers, then a genuinely separate process damages the closed ledger while
        # holding its process lock. Every subsequent renewal must refuse; none may recreate
        # an empty ledger or silently repair/overwrite the damage.
        store2 = FileRuntimeStateStore(path, owner_id="coordinator-A", clock=clock)
        claims2 = [store2.claim(intent(i)) for i in range(count)]
        pacers2 = [BeatPacer() for _ in range(count)]
        keepers2 = [LeaseKeeper(store2, intent(i)["intent_id"], claims2[i]["lease_token"],
                                interval_seconds=20.0, waiter=pacers2[i])
                    for i in range(count)]
        for keeper in keepers2:
            keeper.start()
        ctx = multiprocessing.get_context("spawn")
        ready, go = ctx.Event(), ctx.Event()
        proc = ctx.Process(target=corrupt_under_process_lock, args=(path, ready, go))
        proc.start(); assert ready.wait(JOIN_TIMEOUT); go.set(); proc.join(JOIN_TIMEOUT)
        assert proc.exitcode == 0
        for i in range(count):
            pacers2[i].release_beat()
        lost = all(k.wait_for_beats(1, timeout=JOIN_TIMEOUT) and k.lost for k in keepers2)
        still_corrupt = False
        try:
            store2.get_receipt(intent(0)["intent_id"])
        except RuntimeStateCorrupt:
            still_corrupt = True
        print(f"corruptor_exit={proc.exitcode} all_keepers_lost={lost} "
              f"ledger_not_repaired={still_corrupt}")
        for keeper in keepers2:
            keeper.stop()


if __name__ == "__main__":
    main()
