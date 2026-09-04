"""Mutation: put the pre-fix stop()/beat-loop back and prove the new tests catch it.

Usage:  python3 <this> apply | restore
The restore path rewrites the file from the saved original, so the md5 must match exactly.
"""
import pathlib
import sys

SRC = pathlib.Path("scripts/deterministic_workflow/lease_keeper.py")
SAVE = pathlib.Path("artifacts/runs/run_67098fd04388/iteration2/lease_keeper.fixed.py")

FIXED_STOP_START = "    def stop(self) -> bool:"
FIXED_STOP_END = "    # ---- state ----"
PREFIX_STOP = '''    def stop(self) -> None:
        """Stop beating and join the thread.  Safe to call more than once."""
        self._stop.set()
        cancel = getattr(self._waiter, "cancel", None)
        if callable(cancel):
            cancel()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(self._join_seconds)

    def __enter__(self) -> "LeaseKeeper":
        return self.start()

    def __exit__(self, *exc_info) -> bool:
        self.stop()
        return False

'''


def apply() -> None:
    text = SRC.read_text()
    SAVE.write_text(text)
    head, _, rest = text.partition(FIXED_STOP_START)
    _, _, tail = rest.partition(FIXED_STOP_END)
    text = head + PREFIX_STOP + FIXED_STOP_END + tail
    # ... and drop the revocation re-checks the beat loop grew.
    text = text.replace("""            if self._revoked.is_set():
                return
            try:
                self._runtime_state.heartbeat""", """            try:
                self._runtime_state.heartbeat""")
    text = text.replace("""            if self._revoked.is_set():
                # Revoked while this renewal was in flight""", """            if False:
                # Revoked while this renewal was in flight""")
    SRC.write_text(text)


def restore() -> None:
    SRC.write_text(SAVE.read_text())
    SAVE.unlink()


if __name__ == "__main__":
    {"apply": apply, "restore": restore}[sys.argv[1]]()
