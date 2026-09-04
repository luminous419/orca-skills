import threading
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts"))
from deterministic_workflow.lease_keeper import LeaseKeeper


class Wedge:
    lease_seconds = 60.0

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def heartbeat(self, intent_id, token):
        self.calls += 1
        self.entered.set()
        self.release.wait(5.0)


class Pacer:
    def __init__(self):
        self.go = threading.Event()

    def __call__(self, stop, interval):
        self.go.wait(5.0)
        self.go.clear()
        return False

    def cancel(self):
        self.go.set()


wedge = Wedge()
pacer = Pacer()
keeper = LeaseKeeper(wedge, "review-intent", "token", interval_seconds=20.0,
                     waiter=pacer, join_seconds=0.05)
before = {t.ident for t in threading.enumerate()}
keeper.start()
pacer.go.set()
assert wedge.entered.wait(1.0)
clean = keeper.stop()
thread = keeper._thread
print(f"stop_clean={clean} orphaned={keeper.orphaned} revoked={keeper.revoked}")
print(f"handle_retained={thread is not None} alive_after_stop={thread.is_alive()}")
print(f"calls_after_stop={wedge.calls} beats_after_stop={keeper.beats}")
wedge.release.set()
thread.join(1.0)
time.sleep(0.01)
print(f"alive_after_release={thread.is_alive()} calls_after_release={wedge.calls} beats_after_release={keeper.beats}")
print(f"new_keeper_threads={[(t.name, t.is_alive()) for t in threading.enumerate() if t.ident not in before and t.name.startswith('lease-keeper-')]}")
assert clean is False
assert keeper.orphaned and keeper.revoked
assert wedge.calls == 1 and keeper.beats == 0
assert not thread.is_alive()
