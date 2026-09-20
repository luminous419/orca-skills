"""OS-48 locks for REVIEW_IMPLEMENTATION_iteration4.md F-009, each re-running the reviewer's
construction through the REAL watcher / runtime (`evidence/review_implementation_i4/
probe_discovery_*_real.py`): a cooperative root forks a DETACHED helper (own session, SIGHUP
ignored, alive long after the root) and completes; discovery evidence is then made
unreadable in one specific way, and the question is what the supervisor's ownership
accounting SAYS about the helper it could not see.

* a failed native process listing (the independent `kern.proc.all` read refused, through the
  real `_libproc_list_all_pids`) is `descendants_unknown`, never an empty candidate list;
* a listing that will not settle (`listallpids_unstable`) is `descendants_unknown`;
* a denied `/proc` listing in the Linux watcher is `descendants_unknown`;
* a helper whose `PROC_PIDTBSDINFO` is refused is re-read from the independent `kern.proc.pid`
  source and becomes a POSITIVE member (the named `descendants_unreaped` residual);
* a helper unreadable by EVERY identity source is `descendants_unknown` with its pid named;
* the readable control names the helper positively and reports discovery `readable`;
* the accounting is durable: an adopting successor reads the same `descendants_unknown`;
* nothing unknown is ever signalled -- the helper is alive with its pinned identity after
  `_reclaim`; the capture fence stays valid (this is a G2 accounting residual).
"""
from __future__ import annotations

import errno
import json
import os
import signal
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.deterministic_workflow import standalone_capture as capture_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_pty as pty_supervisor  # noqa: E402
from scripts.os48_cut_harness import settle, successor  # noqa: E402
from scripts.os48_lock_support import PYTHON, Room, spawn_session  # noqa: E402

DARWIN_ONLY = unittest.skipUnless(sys.platform == "darwin", "OS-48 libproc evidence seams are darwin's (probe_03)")
LINUX_ONLY = unittest.skipUnless(sys.platform == "linux", "the /proc discovery walk is Linux's")

#: The reviewer's root: it forks a detached helper (setsid, SIGHUP ignored, 30 s), waits for
#: the helper's "ready" byte, then waits for the test's ACKNOWLEDGED ordering gate
#: (`$OS48_GO`, written by the test once the watcher's ledger shows what the lock needs --
#: REVIEW_IMPLEMENTATION_iteration5 F-010 replaced the earlier timed linger), then emits its
#: bound completion record and exits.  The gate has a 15 s ceiling so a broken test cannot
#: wedge the root; the reviewer's exact no-gate construction is re-run verbatim under
#: `evidence/implementation/i5_reviewer_probes/`.
_ROOT = """import os,time,json,signal
r,w=os.pipe()
if os.fork()==0:
    os.close(r);os.setsid();signal.signal(signal.SIGHUP,signal.SIG_IGN)
    open(%r,"w").write(json.dumps(dict(pid=os.getpid())))
    os.write(w,b"r");os.close(w);time.sleep(30);os._exit(0)
os.close(w);os.read(r,1);os.close(r)
deadline=time.monotonic()+15
while not os.path.exists(os.environ["OS48_GO"]) and time.monotonic()<deadline: time.sleep(0.005)
os.write(1,(json.dumps(dict(type="result",is_error=False,session_id=os.environ["OS48_TEST_SID"]))+"\\n").encode());os._exit(0)
"""


class _HelperCase(unittest.TestCase):
    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)
        self.info = self.room.path / "helper.json"
        self.helper = 0
        self.helper_start = 0
        self.addCleanup(self._kill_helper)

    def _kill_helper(self) -> None:
        if self.helper and self.helper_start and pty_supervisor.proc_start_ticks(self.helper) == self.helper_start:
            try:
                os.kill(self.helper, signal.SIGKILL)
            except OSError:
                pass

    def _helper_pid(self) -> int | None:
        try:
            return int(json.loads(self.info.read_text())["pid"]) if self.info.exists() else None
        except (OSError, ValueError, KeyError):
            return None

    def _run(self, run_id: str, *, patches=(), ack=None):
        """Spawn the reviewer's root under ``patches`` (inherited by the forked watcher), open
        the root's ordering gate when ``ack`` is satisfied (``"attributed"``: the watcher's
        ledger names the helper as an observed member -- an acknowledged ordering, not a
        timer; a callable: its own predicate over the session; ``None``: at once), await its completion, then pin the helper's identity NOW
        (it must still be alive)."""
        agent = self.room.path / f"root-{run_id}.py"
        agent.write_text(_ROOT % str(self.info))
        go = self.room.path / f"go-{run_id}"
        stack = [p.__enter__() for p in patches]
        try:
            session, sentinel = spawn_session(self.room, "", run_id=run_id, argv=[PYTHON, str(agent)], image=PYTHON,
                                              binding_mode="session_field", binding_field="session_id",
                                              pump_until_sentinel=False, extra_env={"OS48_GO": str(go)})
            deadline = time.time() + 20
            opened = False
            while not sentinel.exists() and time.time() < deadline:
                session.pump(timeout_ms=20)
                if not opened and self._ack_satisfied(session, ack):
                    go.write_text("go")
                    opened = True
            self.assertTrue(sentinel.exists(), "the watcher never wrote the sentinel")
            self.assertTrue(opened, f"the ordering gate {ack!r} was never satisfied")
            session.pump(timeout_ms=50)
            result = session.await_completion()
        finally:
            for p in reversed(patches):
                p.__exit__(None, None, None)
        del stack
        self.helper = self._helper_pid() or 0
        self.helper_start = pty_supervisor.proc_start_ticks(self.helper) if self.helper else 0
        self.assertGreater(self.helper_start, 0, "the detached helper is not alive after the root completed")
        self.assertEqual(result["state"], "COMPLETED", result)
        return session, sentinel

    def _ack_satisfied(self, session, ack) -> bool:
        if ack is None:
            return True
        if callable(ack):
            return bool(ack(session))
        helper = self._helper_pid()
        if ack == "attributed":
            if not helper:
                return False
            ledger = pty_supervisor.read_ledger(session._members_path())
            return any(r.get("event") == pty_supervisor.MEMBER_EVENT_OBSERVED
                       and int((r.get("identity") or {}).get("pid") or 0) == helper for r in ledger["records"])
        raise AssertionError(ack)

    def _info_for(self, session, sentinel) -> dict:
        return {"supervisor_pid": os.getpid(), "leader_pid": session.pty["leader_pid"],
                "agent_pid": session.pty["pid"], "session_id": session.session_id,
                "incarnation": session.incarnation, "fence": session.fence,
                "fence_nonce": session.fence_nonce, "capture": str(session.capture.path),
                "sentinel": str(sentinel), "art": str(self.room.path / "art"), "run_id": session.run_id}

    def _state(self, session) -> dict:
        return json.loads(Path(os.fsdecode(session._members_path()) + ".state.json").read_text())

    def _ledger_discovery_records(self, session) -> list[dict]:
        return [r for r in pty_supervisor.read_ledger(session._members_path())["records"]
                if r.get("event") == pty_supervisor.MEMBER_EVENT_DISCOVERY_UNREADABLE]

    def _assert_unknown(self, session, residual, *, counter: str, reason_prefix: str, pids=()) -> dict:
        """The named outcome, the unknown entry, the durable state block and the ledger record
        all agree; the fence is valid; `_reclaim` journals the row; nothing is signalled."""
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)
        self.assertEqual(residual["discovery"], "unreadable")
        self.assertEqual(residual["alive"], [], residual)
        entries = [u for u in residual["unknown"] if u.get("discovery") == "unreadable"]
        self.assertEqual(len(entries), 1, residual["unknown"])
        entry = entries[0]
        self.assertGreaterEqual(int(entry[counter] or 0), 1, entry)
        self.assertTrue(any(r.startswith(reason_prefix) for r in entry["reasons"]), entry["reasons"])
        for pid in pids:
            self.assertIn(pid, entry["pids"], entry)
        state = self._state(session)
        self.assertGreaterEqual(int(state["discovery"][counter]), 1, state)
        self.assertEqual(int(state["failed"]), 0, state)                 # appends succeeded: this is SEPARATE accounting
        records = self._ledger_discovery_records(session)
        self.assertGreaterEqual(len(records), 1, "no durable discovery_unreadable ledger record")
        self.assertTrue(all(r["fence"] == session.fence for r in records))
        self.assertEqual(int(state["appended"]), len(pty_supervisor.read_ledger(session._members_path())["records"]))
        # the capture fence is untouched by an accounting residual
        self.assertEqual(session._boundary["fence"]["fence"], session.fence)
        self.assertTrue(session.capture.completion_is_answerable()["answerable"])
        session._reclaim(reason="lock-f009")
        rows = [r for r in session.journal.rows_for(session.intent_id) if r.get("event") == "descendants_unreaped"]
        self.assertEqual(len(rows), 1, "no named residual row")
        self.assertEqual(rows[0]["source_vocabulary"]["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)
        self.assertEqual(pty_supervisor.proc_start_ticks(self.helper), self.helper_start,
                         "the unknown helper was signalled")
        return entry


# =====================================================================================
# F-009 -- failed / changing listings
# =====================================================================================
class F009ListingUnreadableTests(_HelperCase):
    @DARWIN_ONLY
    def test_a_failed_native_listing_is_descendants_unknown(self) -> None:
        """`probe_discovery_sysctl_unreadable_real`: the independent `kern.proc.all` read is
        refused inside the real `_libproc_list_all_pids` -> every pass is
        `listing_unreadable:listallpids_crosscheck_unreadable`, the residual is
        `descendants_unknown`, and `_reclaim` journals it over the live helper."""
        session, _sentinel = self._run("f009-listing", patches=(patch.object(pty_supervisor, "_sysctl_all_pids", return_value=None),))
        residual = session.membership_residual()
        entry = self._assert_unknown(session, residual, counter="listing_unreadable",
                                     reason_prefix="listing_unreadable:listallpids_crosscheck_unreadable")
        self.assertEqual(entry["listing_unstable"], 0)
        self.assertGreaterEqual(entry["passes"], 1)

    @DARWIN_ONLY
    def test_an_unstable_listing_is_descendants_unknown(self) -> None:
        """A listing that never settles (`listallpids_unstable` after the bounded retries) is
        changing evidence: accounted `listing_unstable`, the residual is `descendants_unknown`."""
        session, _sentinel = self._run("f009-unstable", patches=(
            patch.object(pty_supervisor, "_libproc_list_all_pids", return_value=(None, "listallpids_unstable")),))
        residual = session.membership_residual()
        entry = self._assert_unknown(session, residual, counter="listing_unstable",
                                     reason_prefix="listing_unstable:listallpids_unstable")
        self.assertEqual(entry["listing_unreadable"], 0)

    @LINUX_ONLY
    def test_a_denied_proc_listing_is_descendants_unknown(self) -> None:
        """`probe_discovery_linux_denied_real`: `os.listdir("/proc")` raises EACCES in the real
        watcher -> `listing_unreadable:proc_listdir:PermissionError:13`, `descendants_unknown`."""
        real_listdir = os.listdir

        def denied(path="."):
            if os.fsdecode(path) != "/proc":
                return real_listdir(path)
            raise PermissionError(errno.EACCES, "lock: proc discovery denied")
        session, _sentinel = self._run("f009-proc", patches=(patch.object(os, "listdir", side_effect=denied),))
        residual = session.membership_residual()
        self._assert_unknown(session, residual, counter="listing_unreadable",
                             reason_prefix="listing_unreadable:proc_listdir:PermissionError:13")


# =====================================================================================
# F-009 -- unreadable candidate identities
# =====================================================================================
class F009CandidateIdentityTests(_HelperCase):
    def _deny_info(self):
        real = pty_supervisor._process_info

        def denied(pid):
            return None if self._helper_pid() == pid else real(pid)
        return patch.object(pty_supervisor, "_process_info", side_effect=denied)

    def _deny_fallback(self):
        real = pty_supervisor._process_info_fallback

        def denied(pid):
            return None if self._helper_pid() == pid else real(pid)
        return patch.object(pty_supervisor, "_process_info_fallback", side_effect=denied)

    @DARWIN_ONLY
    def test_a_refused_bsdinfo_is_resolved_by_the_independent_read(self) -> None:
        """`probe_discovery_helper_identity_unreadable_real`: `PROC_PIDTBSDINFO` refuses only the
        helper -> the watcher re-reads it from `kern.proc.pid` and attributes it POSITIVELY: the
        residual names the live descendant (`descendants_unreaped`), discovery stays readable."""
        session, _sentinel = self._run("f009-bsdinfo", patches=(self._deny_info(),), ack="attributed")
        residual = session.membership_residual()
        # superseded by OS-48 i7 (conservative model): the root forked -> `descendants_unknown`
        # (`fork_coalesced`); what this lock asserts is the POSITIVE attribution through the
        # independent read and that no candidate/parent unreadable is recorded for it.
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)
        entry = [u for u in residual["unknown"] if u.get("discovery") == "unreadable"][0]
        self.assertEqual((entry["candidates_unreadable"], entry["parents_unreadable"]), (0, 0), entry)
        alive = [m for m in residual["alive"] if m["pid"] == self.helper]
        self.assertEqual(len(alive), 1, residual)
        self.assertEqual((alive[0]["start_id"], alive[0]["role"]), (self.helper_start, pty_supervisor.MEMBER_ROLE_DESCENDANT))
        self.assertEqual(self._state(session)["discovery"]["candidates_unreadable"], 0)
        session._reclaim(reason="lock-f009")
        rows = [r for r in session.journal.rows_for(session.intent_id) if r.get("event") == "descendants_unreaped"]
        self.assertEqual(len(rows), 1)
        self.assertEqual([m["pid"] for m in rows[0]["source_vocabulary"]["alive"]], [self.helper])
        self.assertEqual(pty_supervisor.proc_start_ticks(self.helper), self.helper_start, "the member was signalled")

    def test_a_helper_unreadable_by_every_source_is_descendants_unknown(self) -> None:
        """Every identity source refuses the helper while the kernel still holds its pid (not a
        zombie, not gone) -> `candidate_identity_unreadable` with the pid named."""
        patches = (self._deny_info(),) + ((self._deny_fallback(),) if sys.platform == "darwin" else ())
        session, _sentinel = self._run("f009-both", patches=patches)
        residual = session.membership_residual()
        entry = self._assert_unknown(session, residual, counter="candidates_unreadable",
                                     reason_prefix="candidate_identity_unreadable:process_info_unreadable",
                                     pids=(self.helper,))
        self.assertEqual(entry["listing_unreadable"], 0)
        # the pid is accounted ONCE however many passes saw it
        self.assertEqual(self._state(session)["discovery"]["pids"].count(self.helper), 1)

    def test_the_readable_control_names_the_helper_and_discovery_readable(self) -> None:
        """`probe_membership_real`: with nothing refused the helper is a positive member, the
        residual names it alive, discovery is `readable` with zero unreadable counters, and no
        `discovery_unreadable` record exists."""
        session, _sentinel = self._run("f009-control", ack="attributed")
        residual = session.membership_residual()
        # superseded by OS-48 i7 (conservative model): the helper is a POSITIVE alive member;
        # the residual is `descendants_unknown` because the root forked (`fork_coalesced`)
        # and the helper's own watch was registered after its birth (`fork_watch_gap`) --
        # nothing listing/candidate/parent-unreadable is recorded.
        # (Linux has no fork events: the subreaper attributes positively and the pre-reclaim
        # residual of the control is clean.)
        if sys.platform == "darwin":
            self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)
        else:
            self.assertIsNone(residual["outcome"], residual)
        self.assertIn(self.helper, [m["pid"] for m in residual["alive"]], residual)
        state = self._state(session)["discovery"]
        self.assertEqual((state["listing_unreadable"], state["listing_unstable"], state["candidates_unreadable"], state["parents_unreadable"]), (0, 0, 0, 0), state)
        self.assertEqual(state["root_watch"], "registered" if sys.platform == "darwin" else "subreaper")
        kinds = {r["kind"] for r in self._ledger_discovery_records(session)} - {pty_supervisor.DISCOVERY_WATCH_ENDED}
        self.assertEqual(kinds, {pty_supervisor.DISCOVERY_FORK_COALESCED, pty_supervisor.DISCOVERY_FORK_WATCH_GAP}
                         if sys.platform == "darwin" else set(), kinds)

    @DARWIN_ONLY
    def test_the_independent_read_agrees_with_bsdinfo_and_refuses_a_gone_pid(self) -> None:
        me = os.getpid()
        self.assertEqual(pty_supervisor._darwin_kinfo(me), pty_supervisor._darwin_bsdinfo(me))
        self.assertEqual(pty_supervisor._darwin_kinfo(me)[0], os.getppid())
        child = os.fork()
        if child == 0:
            os._exit(0)
        os.waitpid(child, 0)
        self.assertIsNone(pty_supervisor._darwin_kinfo(child))             # reaped: zero bytes, never a stale identity
        self.assertIsNone(pty_supervisor._darwin_kinfo(2 ** 22 + 12345))


# =====================================================================================
# F-009 -- persistence after adoption
# =====================================================================================
class F009AdoptionPersistenceTests(_HelperCase):
    @DARWIN_ONLY
    def test_a_successor_reads_the_same_descendants_unknown(self) -> None:
        """The discovery accounting lives in the ledger + state file, so an adopting successor
        (masterless, from disk) reports the same `descendants_unknown` and journals its own row."""
        session, sentinel = self._run("f009-adopt", patches=(patch.object(pty_supervisor, "_sysctl_all_pids", return_value=None),))
        first = session.membership_residual()
        self.assertEqual(first["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)
        s = successor(self.room, self._info_for(session, sentinel))
        out = settle(s)
        self.assertEqual(out["state"], "COMPLETED", out)
        second = s.membership_residual()
        self.assertEqual(second["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, second)
        self.assertEqual([u for u in second["unknown"] if u.get("discovery")],
                         [u for u in first["unknown"] if u.get("discovery")])
        s._reclaim(reason="lock-f009-adopted")
        rows = [r for r in s.journal.rows_for(s.intent_id) if r.get("event") == "descendants_unreaped"]
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[-1]["source_vocabulary"]["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)
        self.assertEqual(pty_supervisor.proc_start_ticks(self.helper), self.helper_start)

    @LINUX_ONLY
    def test_a_successor_reads_the_same_descendants_unknown_linux(self) -> None:
        real_listdir = os.listdir

        def denied(path="."):
            if os.fsdecode(path) != "/proc":
                return real_listdir(path)
            raise PermissionError(errno.EACCES, "lock: proc discovery denied")
        session, sentinel = self._run("f009-adopt-linux", patches=(patch.object(os, "listdir", side_effect=denied),))
        s = successor(self.room, self._info_for(session, sentinel))
        self.assertEqual(settle(s)["state"], "COMPLETED")
        self.assertEqual(s.membership_residual()["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
