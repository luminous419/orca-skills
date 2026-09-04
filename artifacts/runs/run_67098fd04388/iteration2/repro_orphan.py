"""Print the orphan directly: stop() a keeper whose renewal is wedged inside the ledger."""
import threading

from scripts.deterministic_workflow.lease_keeper import LeaseKeeper


class WedgedLedger:
    lease_seconds = 60.0

    def __init__(self):
        self.entered, self.finish, self.calls = threading.Event(), threading.Event(), 0

    def heartbeat(self, intent_id, lease_token):
        self.calls += 1
        self.entered.set()
        self.finish.wait(30.0)
        return {"intent_id": intent_id}


class Pacer:
    def __init__(self):
        self.c, self.pending, self.cancelled = threading.Condition(), 0, False

    def __call__(self, stop, interval):
        with self.c:
            self.c.wait_for(lambda: self.pending > 0 or self.cancelled, timeout=30.0)
            if self.cancelled or stop.is_set():
                return True
            self.pending -= 1
        return False

    def cancel(self):
        with self.c:
            self.cancelled = True
            self.c.notify_all()

    def release(self):
        with self.c:
            self.pending += 1
            self.c.notify_all()


ledger, pacer = WedgedLedger(), Pacer()
keeper = LeaseKeeper(ledger, "intent_x", "token", interval_seconds=20.0, waiter=pacer,
                     join_seconds=0.3)
keeper.start()
pacer.release()
ledger.entered.wait(30.0)

result = keeper.stop()
print(f"stop() returned: {result!r}")
print(f"keeper._thread is now: {keeper._thread!r}")
print(f"keeper.orphaned: {getattr(keeper, 'orphaned', '<attribute absent>')}")
live = [t for t in threading.enumerate() if t.name.startswith("lease-keeper")]
for t in live:
    print(f"   STILL LIVE: name={t.name} alive={t.is_alive()} daemon={t.daemon}")
print(f"live lease-keeper threads after stop(): {len(live)}")

pacer.release()          # invite another renewal
ledger.finish.set()      # and let the wedged one complete
for t in live:
    t.join(30.0)
print(f"heartbeat calls in total: {ledger.calls}")
print(f"beats the keeper counted: {keeper.beats}")
