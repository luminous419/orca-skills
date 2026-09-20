"""OS-48 evidence locks (run_f820764749d6, DESIGN §7 L-06 / L-07 / L-08, §2.6 fail-closed):
every evidence read is a `(value, EvidenceState)`; a short / denied / stale / inconsistent
read is NAMED `unreadable` (or `inconsistent`) and refuses -- on the signal path as
`identity_unreadable` (no signal), on the diagnostic enumeration as `unreadable` rows.  The
libproc fault seams are probe_03's constructions (3a short pid fill, 3b partial BSDINFO /
fd listing, 3c denied, 3d stale ENOENT, count != fill).

RED at b9aecce: the enumeration was a DECISION input (`proven_absent` gated the release) and a
short BSDINFO decoded from the zero-filled buffer; here the enumeration reaches no decision
(test_os48_ownership_locks L-14) and every fault is named.
"""
from __future__ import annotations

import ctypes
import errno
import os
import sys
import time
import unittest

from scripts.deterministic_workflow import standalone_capture as capture_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_identity as identity  # noqa: E402
from scripts.deterministic_workflow import standalone_interrupt as interrupt_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_pty as pty_supervisor  # noqa: E402
from scripts.os48_lock_support import KillSpy, Room, sh_profile  # noqa: E402
from scripts.test_os37_pty_supervisor import (BOOT_ID, START_ID, TTY,  # noqa: E402
                                              record, snapshot)

DARWIN_REASON = "OS-48 libproc evidence seams are darwin's (probe_03)"
DARWIN_ONLY = unittest.skipUnless(sys.platform == "darwin", DARWIN_REASON)


# =====================================================================================
# L-07 -- a partial identity read is `identity_unreadable` on the SIGNAL path: no signal
# =====================================================================================
class L07SignalPathIdentityTests(unittest.TestCase):
    def _ladder(self, reader):
        room = Room()
        self.addCleanup(room.close)
        spy = KillSpy()
        via: list[int] = []
        rows = [{"pid": snapshot()["rows"][0]["pid"], "ppid": 1, "pgid": snapshot()["rows"][0]["pgid"],
                 "sid": snapshot()["rows"][0]["sid"], "tty": TTY, "stat": "Ss"}]   # NO axes: read at decision time
        result = interrupt_mod.interrupt(
            "i-l07", "stop", record=record(), profile=sh_profile(str(room.path)),
            table_reader=lambda _t: {"tty": TTY, "captured_at": time.time(), "rows": tuple(rows),
                                     "readable": True},
            supervisor_pid=999, killpg=spy.send_group, kill=spy.send_one,
            sleep=lambda _s: None, watcher=lambda sig: via.append(sig) or "sent",
            identity_reader=reader)
        return result, spy, via

    def test_an_unreadable_start_identity_refuses_and_sends_nothing(self) -> None:
        result, spy, via = self._ladder(lambda pid: {"start_id": 0, "start_state": "unreadable",
                                                     "boot_id": BOOT_ID})
        self.assertEqual(spy.kill + spy.killpg, [], result)
        self.assertEqual(via, [], "a signal was delivered on an unreadable identity")
        self.assertIn(identity.IDENTITY_UNREADABLE, str(result), result)

    def test_a_missing_boot_id_refuses_and_sends_nothing(self) -> None:
        result, spy, via = self._ladder(lambda pid: {"start_id": START_ID, "start_state": "final",
                                                     "boot_id": ""})
        self.assertEqual(spy.kill + spy.killpg, [], result)
        self.assertEqual(via, [], result)
        self.assertIn(identity.IDENTITY_UNREADABLE, str(result), result)

    def test_a_changed_start_identity_refuses_and_sends_nothing(self) -> None:
        result, spy, via = self._ladder(lambda pid: {"start_id": START_ID + 1, "start_state": "final",
                                                     "boot_id": BOOT_ID})
        self.assertEqual(spy.kill + spy.killpg, [], result)
        self.assertEqual(via, [], result)
        self.assertIn(identity.IDENTITY_CHANGED, str(result), result)

    def test_a_matching_identity_delivers_through_the_watcher_only(self) -> None:
        result, spy, via = self._ladder(lambda pid: {"start_id": START_ID, "start_state": "final",
                                                     "boot_id": BOOT_ID})
        self.assertEqual(spy.kill + spy.killpg, [], result)
        self.assertTrue(via, result)


@DARWIN_ONLY
class L07DarwinIdentityDecodeTests(unittest.TestCase):
    def test_a_short_bsdinfo_read_is_never_decoded(self) -> None:
        """probe_03 3b: 32 / 135 / 137-byte `PROC_PIDTBSDINFO` returns -> start ticks 0 ->
        `read_identity` state `unreadable` for a LIVE pid (never `final`, never `absent`)."""
        realL = pty_supervisor._libproc_handle()
        me = os.getpid()
        for short in (32, 135, 137):
            with self.subTest(bytes=short):
                class _Lib:
                    def __getattr__(self, name):
                        return getattr(realL, name)

                    class proc_pidinfo:      # a callable with argtypes/restype attributes
                        argtypes = None
                        restype = None

                        def __new__(cls, pid, flavor, arg, buf, size):
                            return short
                real_handle = pty_supervisor._libproc_handle
                pty_supervisor._libproc_handle = lambda: _Lib()
                try:
                    self.assertEqual(pty_supervisor._darwin_start_ticks(me), 0)
                    observed = pty_supervisor.read_identity(me)
                finally:
                    pty_supervisor._libproc_handle = real_handle
                self.assertEqual(observed["start_state"], capture_mod.EVIDENCE_UNREADABLE, observed)
                self.assertEqual(observed["start_id"], 0)


# =====================================================================================
# L-06 / L-08 -- the diagnostic enumeration names every fault; it is never absence
# =====================================================================================
@DARWIN_ONLY
class L06L08DiagnosticEnumerationTests(unittest.TestCase):
    def setUp(self) -> None:
        import pty as _pty
        self.master, self.slave = _pty.openpty()
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)
        self.slave_name = os.ttyname(self.slave)

    def test_a_short_pid_fill_is_unreadable_by_name(self) -> None:
        """probe_03 3a: `proc_listallpids` fill that reaches the buffer capacity on every
        attempt (truncated / growing) -> the listing is refused `listallpids_truncated` and
        the diagnostic is `unreadable` with the reason NAMED."""
        realL = pty_supervisor._LIBPROC

        class _Trunc:
            def __getattr__(self, name):
                return getattr(realL, name)

            def proc_listallpids(self, buf, size):
                if buf is None:
                    return 4
                return size // 4                       # fills to capacity every time
        pty_supervisor._LIBPROC = _Trunc()
        self.addCleanup(setattr, pty_supervisor, "_LIBPROC", realL)
        pids, reason = pty_supervisor._libproc_list_all_pids()
        self.assertIsNone(pids)
        self.assertEqual(reason, "listallpids_truncated")
        holders = pty_supervisor.slave_device_holders(self.slave_name)
        self.assertEqual(holders["state"], "unreadable", holders)
        self.assertEqual(holders["unenumerable"][0]["errno"], "listallpids_truncated")

    def test_a_count_that_disagrees_with_the_fill_is_unreadable(self) -> None:
        """count != fill seam: the count says N, the fill returns 0 (EPERM-shaped) -> named."""
        realL = pty_supervisor._LIBPROC

        class _Zero:
            def __getattr__(self, name):
                return getattr(realL, name)

            def proc_listallpids(self, buf, size):
                if buf is None:
                    return 64
                ctypes.set_errno(errno.EPERM)
                return 0
        pty_supervisor._LIBPROC = _Zero()
        self.addCleanup(setattr, pty_supervisor, "_LIBPROC", realL)
        pids, reason = pty_supervisor._libproc_list_all_pids()
        self.assertIsNone(pids)
        self.assertTrue(str(reason).startswith("listallpids_fill:"), reason)

    def test_denied_stale_and_short_fd_reads_are_unreadable_never_absence(self) -> None:
        """probe_03 3c/3d + F4: the SAME pid under a denied listing, a stale (revoked, ENOENT)
        per-fd read and a short per-fd read: three distinct NAMED `unreadable` rows; the state
        never says `none_observed` while any of them is present."""
        me = os.getpid()
        cases = {
            "listing_denied": ("_libproc_list_vnode_fds", lambda pid, _r=pty_supervisor._libproc_list_vnode_fds:
                               (None, errno.EPERM) if pid == me else _r(pid)),
            "stale_revoked_fd": ("_libproc_fd_devino", lambda pid, fd, _r=pty_supervisor._libproc_fd_devino:
                                 (None, errno.ENOENT) if pid == me else _r(pid, fd)),
            "fd_denied": ("_libproc_fd_devino", lambda pid, fd, _r=pty_supervisor._libproc_fd_devino:
                          (None, errno.EPERM) if pid == me else _r(pid, fd)),
        }
        for expected, (name, seam) in cases.items():
            with self.subTest(fault=expected):
                real = getattr(pty_supervisor, name)
                setattr(pty_supervisor, name, seam)
                try:
                    holders = pty_supervisor.slave_device_holders(self.slave_name, exclude_pids=())
                finally:
                    setattr(pty_supervisor, name, real)
                rows = [u for u in holders["unenumerable"] if u.get("pid") == me]
                self.assertEqual([u.get("errno") for u in rows], [expected], holders)
                self.assertEqual(holders["state"], "unreadable", holders)
                self.assertNotIn(me, holders["gone"])
                self.assertNotIn(me, holders["other_uid"])

    def test_the_word_proven_never_appears_in_a_diagnostic_state(self) -> None:
        holders = pty_supervisor.slave_device_holders(self.slave_name, exclude_pids=(os.getpid(),))
        self.assertIn(holders["state"], ("present", "unreadable", "none_observed"))
        self.assertNotIn("proven", holders["state"])
        tri = pty_supervisor._slave_holder_state({"foreground_group_present": False,
                                                  "foreground_probe": "complete", "rows": [],
                                                  "unreadable": [], "proc_scan": "complete"})
        self.assertEqual(tri, "none_observed")


class L08EvidenceStateTests(unittest.TestCase):
    def test_every_evidence_read_lands_in_a_named_state(self) -> None:
        self.assertEqual(capture_mod.EVIDENCE_STATES,
                         frozenset({"present", "final", "unreadable", "inconsistent", "unknown"}))

    def test_an_unreadable_generation_directory_refuses_the_claim(self) -> None:
        room = Room()
        self.addCleanup(room.close)
        missing = room.path / "absent-dir"
        highest, rec, state = capture_mod.read_generations(missing, "inc")
        self.assertEqual((highest, rec), (0, None))
        self.assertEqual(state, capture_mod.EVIDENCE_UNREADABLE)
        generation = capture_mod.make_owner_generation(
            fence="s:inc", generation=1, owner_role=capture_mod.OWNER_SUPERVISOR,
            owner={"pid": os.getpid(), "start_id": 1, "boot_id": "b"},
            claim_reason="test", superseded=None, death_evidence="", claimed_at="t")
        self.assertEqual(capture_mod.claim_generation(missing, "inc", generation, {
            "predecessor_generation": 0, "predecessor": {}, "relinquish_record": False,
            "death_witness": "final", "highest_owner_alive": None}), "identity_unreadable")

    def test_a_torn_or_duplicated_marker_is_inconsistent_not_final(self) -> None:
        nonce = "b" * 32
        marker = capture_mod.marker_bytes(nonce)
        twice = b"x\n" + marker + b"y\n" + marker
        self.assertEqual(capture_mod.marker_span(twice, nonce)[2], capture_mod.EVIDENCE_INCONSISTENT)
        torn = b"x\n" + marker[:-3]
        self.assertEqual(capture_mod.marker_span(torn, nonce)[2], capture_mod.EVIDENCE_UNKNOWN)
        self.assertEqual(capture_mod.marker_span(b"x\n" + marker, nonce)[2], capture_mod.EVIDENCE_FINAL)

    def test_a_fence_with_an_unreadable_prefix_is_a_named_mismatch(self) -> None:
        room = Room()
        self.addCleanup(room.close)
        capture = room.path / "capture.log"
        capture.write_bytes(b"short")
        ident = {"pid": 1, "start_id": 1, "boot_id": "b"}
        record = {"boundary": {"offset_n": 99, "sha256_prefix": "0" * 64},
                  "exit": {"how": "waitpid_by_parent", "code": 0},
                  "emitter": ident, "owner": {"owner": ident}}
        bound = capture_mod.fence_matches(record, capture=capture, sentinel_code=None,
                                          sentinel_present=False)
        self.assertEqual(bound["reason"], "capture_shorter_than_boundary", bound)
        record["boundary"]["offset_n"] = 5
        bound = capture_mod.fence_matches(record, capture=capture, sentinel_code=None,
                                          sentinel_present=False)
        self.assertEqual(bound["reason"], "capture_digest_mismatch", bound)


if __name__ == "__main__":
    unittest.main()
