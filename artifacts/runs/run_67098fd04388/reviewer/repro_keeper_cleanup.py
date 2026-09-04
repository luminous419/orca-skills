import threading

from scripts.deterministic_workflow.lease_keeper import LeaseKeeper


entered = threading.Event()
release = threading.Event()


class BlockingLedger:
    def heartbeat(self, intent_id, lease_token):
        entered.set()
        release.wait(5)


class ImmediateOnce:
    def __init__(self):
        self.used = False

    def __call__(self, stop, interval):
        if not self.used:
            self.used = True
            return False
        return stop.wait(interval)


keeper = LeaseKeeper(BlockingLedger(), "cleanup-repro", "token",
                     interval_seconds=60, waiter=ImmediateOnce(), join_seconds=0.01)
keeper.start()
assert entered.wait(1)
keeper.stop()
alive = [t for t in threading.enumerate() if t.name == "lease-keeper-cleanup-repro"]
print(f"threads_alive_after_stop={len(alive)}")
print(f"keeper_reference_after_stop={keeper._thread!r}")
release.set()
for thread in alive:
    thread.join(1)
print(f"threads_alive_after_release={sum(t.is_alive() for t in alive)}")
