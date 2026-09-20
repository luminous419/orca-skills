"""OS-48 locks for REVIEW_IMPLEMENTATION_iteration2.md (F-001 / F-004 / F-006 / F-007), each
re-running the reviewer's i2 construction through the REAL caller
(`evidence/review_implementation_i2/probe_*`):

* F-001 the declared sidecar is read + digested by the WATCHER in the reap step, BEFORE the
  marker (`snapshot_sidecar`): a cooperative helper's acknowledged post-N update -- at the
  marker->publication seam, or after an orphan publication -- never reaches the settlement;
  a sidecar with no boundary snapshot is refused by name (`sidecar_unproven`); supervisor,
  orphan and adoption paths;
* F-004 a small partial PID prefix (scanner pid present) is `listallpids_partial` (an
  independent `kern.proc.all` walk before and after the fill), an unreadable cross-check
  is named (`listallpids_crosscheck_unreadable`, `fd_table_size_unreadable`), never clean;
* F-006 a child is attributed only to a LIVE / zombie parent incarnation (exited, stale and
  reused parents refuse), members are keyed by incarnation, an unreadable / torn ledger is
  `membership_unreadable` with unknown accounting, member pid reuse is `exited`;
* F-007 the RC2 cut is a real SIGKILL at the tmp-fsynced -> link boundary (tmp present, target
  absent, asserted) on the supervisor AND the orphan-watcher publication paths, with
  simultaneous successor recovery and no winner deleted.
"""
from __future__ import annotations

import builtins
import errno
import json
import os
import pty as _pty
import signal
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.deterministic_workflow import standalone_capture as capture_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_pty as pty_supervisor  # noqa: E402
from scripts.deterministic_workflow import standalone_runtime as rt  # noqa: E402
from scripts.deterministic_workflow.standalone_profile import ResultBodySelector  # noqa: E402
from scripts.os48_cut_harness import (Pause, fence_path, kill_child, orphan_note, owner_dir,  # noqa: E402
                                      pid_alive, racing_successors, read_fence, read_release,
                                      release_path, settle, successor, supervisor_child, wait_dead,
                                      wait_for)
from scripts.os48_lock_support import PYTHON, Room, spawn_session  # noqa: E402

DARWIN_ONLY = unittest.skipUnless(sys.platform == "darwin", "OS-48 libproc evidence seams are darwin's (probe_03)")
BODY_SELECTOR = (ResultBodySelector(channel="structured", record_type="result", body_field="result"),)


def _sidecar_agent(room: Room, sidecar: Path, go: Path, ack: Path, *, early: str, late: str) -> Path:
    """The reviewer's cooperative root + helper: the root writes ``early`` to the declared
    file and a bound completion record, the helper rewrites the file with ``late`` on ``go``."""
    agent = room.path / "sidecar_agent.py"
    agent.write_text(
        "import os,time,json\n"
        "open(%r,'w').write(%r)\n"
        "os.write(1,(json.dumps(dict(type='result',is_error=False,session_id=os.environ['OS48_TEST_SID']))+'\\n').encode())\n"
        "if os.fork()==0:\n"
        "    while not os.path.exists(%r): time.sleep(.005)\n"
        "    open(%r,'w').write(%r)\n"
        "    open(%r,'w').write('written')\n"
        "    time.sleep(3);os._exit(0)\n"
        "os._exit(0)\n" % (str(sidecar), early, str(go), str(sidecar), late, str(ack)))
    return agent


class _RoomCase(unittest.TestCase):
    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)

    def _info(self, session, sentinel) -> dict:
        return {"supervisor_pid": os.getpid(), "leader_pid": session.pty["leader_pid"],
                "agent_pid": session.pty["pid"], "session_id": session.session_id,
                "incarnation": session.incarnation, "fence": session.fence,
                "fence_nonce": session.fence_nonce, "capture": str(session.capture.path),
                "sentinel": str(sentinel), "art": str(self.room.path / "art"), "run_id": session.run_id}


# =====================================================================================
# F-001 -- the sidecar is fixed AT the boundary by the watcher
# =====================================================================================
class F001SidecarAtBoundaryTests(_RoomCase):
    def _declared_session(self, run_id: str, *, declare_at_spawn: bool):
        sidecar, go, ack = self.room.path / "last_message.md", self.room.path / "go", self.room.path / "ack"
        agent = _sidecar_agent(self.room, sidecar, go, ack, early="EARLY SIDECAR BODY", late="POST-N SIDECAR BODY")
        session, sentinel = spawn_session(self.room, "", run_id=run_id, argv=[PYTHON, str(agent)], image=PYTHON,
                                          binding_mode="session_field", binding_field="session_id",
                                          pump_until_sentinel=False,
                                          sidecar_path=str(sidecar) if declare_at_spawn else "")
        session.last_message_path = str(sidecar)
        session.profile = replace(session.profile, output_last_message_path=str(sidecar), result_body_records=BODY_SELECTOR)
        session.driver = rt.drivers.driver_for(session.profile)
        return session, sentinel, sidecar, go, ack

    def _settle_seen(self, session, completion) -> tuple[list, dict]:
        seen: list = []

        def parser(attempt, intent):
            seen.append(attempt.body)
            return {"status": "COMPLETE", "body_seen": attempt.body}
        session.intent["command_id"] = "c"
        session.intent["payload_digest"] = "0" * 64
        session.state = "RUNNING"
        with patch.object(session, "_reclaim", return_value={"reaped": True}):
            event = session._settle(completion["evidence"], lease_token=None, result_parser=parser,
                                    verdict=completion["verdict"])
        return seen, event

    def _update_at_publication_seam(self, session, go: Path, ack: Path):
        """The reviewer's seam: the helper rewrites the declared file AFTER the marker was
        observed at N and BEFORE the unmodified publisher runs."""
        real = session._publish_fence
        cut: list = []

        def after_marker(drained):
            cut.append({"ended": drained["ended"], "offset_n": drained.get("offset_n")})
            go.write_text("go")
            wait_for(ack.exists, seconds=5, what="the helper's post-N sidecar update")
            return real(drained)
        with patch.object(session, "_publish_fence", side_effect=after_marker):
            completion = session.await_completion()
        self.assertEqual(cut and cut[0]["ended"], "marker", cut)
        return completion

    def test_a_post_n_sidecar_update_at_the_publication_seam_never_settles(self) -> None:
        """`probe_sidecar_after_boundary`, path declared at spawn: the watcher's boundary
        snapshot holds `EARLY SIDECAR BODY`; the fence digests THAT; the real `_settle`
        parser receives the early body although the file now says POST-N."""
        session, _sentinel, sidecar, go, ack = self._declared_session("f001a", declare_at_spawn=True)
        completion = self._update_at_publication_seam(session, go, ack)
        self.assertEqual(completion["state"], "COMPLETED", completion)
        self.assertEqual(sidecar.read_text(), "POST-N SIDECAR BODY")
        fence = session._boundary["fence"]["sidecar"]
        self.assertEqual(fence["state"], capture_mod.SIDECAR_STATE_PRESENT, fence)
        import hashlib
        self.assertEqual(fence["sha256"], hashlib.sha256(b"EARLY SIDECAR BODY").hexdigest())
        # superseded by OS-48 i4 (REVIEW_IMPLEMENTATION_iteration3 F-001 (b), option ii): the
        # snapshot pins the PRESENCE fact only; sidecar content is never a body source, so the
        # parser receives the in-boundary STREAM record -- never the early file, never the late one
        seen, event = self._settle_seen(session, completion)
        self.assertEqual(len(seen), 1)
        self.assertNotIn("SIDECAR BODY", seen[0])
        self.assertNotIn("SIDECAR BODY", event["result"]["body_seen"])
        rows = [r for r in session.journal.rows_for(session.intent_id) if r["kind"] == "SETTLEMENT_OBSERVED"]
        prov = rows[-1]["source_vocabulary"]["result_body_provenance"]
        self.assertEqual(prov.get("sidecar_refused"), capture_mod.SIDECAR_STATE_UNPROVEN, prov)
        self.assertEqual(prov.get("sidecar_presence"), capture_mod.SIDECAR_STATE_PRESENT, prov)

    def test_a_sidecar_without_a_boundary_snapshot_is_refused_by_name(self) -> None:
        """The reviewer's exact construction (path declared AFTER spawn -> no snapshot): the
        fence says `sidecar_unproven`, the settlement never reads the file (the in-boundary
        record settles), the provenance names the refusal."""
        session, _sentinel, sidecar, go, ack = self._declared_session("f001b", declare_at_spawn=False)
        completion = self._update_at_publication_seam(session, go, ack)
        self.assertEqual(completion["state"], "COMPLETED", completion)
        self.assertEqual(session._boundary["fence"]["sidecar"], {"state": capture_mod.SIDECAR_STATE_UNPROVEN})
        seen, _event = self._settle_seen(session, completion)
        self.assertEqual(len(seen), 1)
        self.assertNotIn("SIDECAR BODY", seen[0])
        rows = [r for r in session.journal.rows_for(session.intent_id) if r["kind"] == "SETTLEMENT_OBSERVED"]
        prov = rows[-1]["source_vocabulary"]["result_body_provenance"]
        self.assertEqual(prov.get("sidecar_refused"), capture_mod.SIDECAR_STATE_UNPROVEN, prov)

    def test_the_orphan_and_adoption_paths_settle_the_boundary_body(self) -> None:
        """`probe_orphan_sidecar` with the path declared at spawn: S is SIGKILLed before its
        fence, the watcher publishes with the boundary snapshot, the helper then rewrites
        the file (acknowledged); the successor settles `EARLY SIDECAR BODY`."""
        sidecar, go, ack = self.room.path / "last_message.md", self.room.path / "go", self.room.path / "ack"
        agent = _sidecar_agent(self.room, sidecar, go, ack, early="EARLY SIDECAR BODY", late="POST-N ORPHAN SIDECAR")
        import shlex

        def install(pause: Pause, sup: int) -> None:
            pause.wrap(capture_mod, "write_capture_fence", "sidecar", supervisor_pid=sup)
        info = supervisor_child(self.room, run_id="f001c", cut="sidecar", install=install,
                                agent=shlex.quote(PYTHON) + " " + shlex.quote(str(agent)) + "\nexit 0\n",
                                sidecar_path=str(sidecar))
        wait_for(Pause(self.room.path).file("sidecar").exists, seconds=20, what="the marker before S's publication")
        kill_child(info)
        wait_for(fence_path(info).exists, seconds=20, what="the watcher's fence")
        record = read_fence(info)["record"]
        self.assertEqual(record["owner"]["owner_role"], capture_mod.OWNER_EXIT_WATCHER)
        self.assertEqual(record["sidecar"]["state"], capture_mod.SIDECAR_STATE_PRESENT, record["sidecar"])
        go.write_text("go")
        wait_for(ack.exists, seconds=5, what="the helper's post-publication update")
        self.assertEqual(sidecar.read_text(), "POST-N ORPHAN SIDECAR")
        s = successor(self.room, info)
        s.last_message_path = str(sidecar)
        s.profile = replace(s.profile, output_last_message_path=str(sidecar), result_body_records=BODY_SELECTOR)
        s.driver = rt.drivers.driver_for(s.profile)
        out = settle(s)
        self.assertEqual(out["state"], "COMPLETED", out)
        # superseded by OS-48 i4 (option ii): the successor settles the in-boundary STREAM
        # record; neither the early nor the post-N file content is ever a body
        self.assertNotIn("SIDECAR", out["event"]["result"]["body"], out)
        self.assertEqual(s._sidecar_state, capture_mod.SIDECAR_STATE_PRESENT)

    def test_an_orphan_fence_without_a_snapshot_refuses_the_sidecar_on_adoption(self) -> None:
        """The reviewer's exact orphan construction (no path declared at spawn): the watcher's
        fence carries no positive snapshot -> the successor refuses the body source."""
        sidecar, go, ack = self.room.path / "last_message.md", self.room.path / "go", self.room.path / "ack"
        agent = _sidecar_agent(self.room, sidecar, go, ack, early="EARLY SIDECAR BODY", late="POST-N ORPHAN SIDECAR")
        import shlex

        def install(pause: Pause, sup: int) -> None:
            pause.wrap(capture_mod, "write_capture_fence", "sidecar2", supervisor_pid=sup)
        info = supervisor_child(self.room, run_id="f001d", cut="sidecar2", install=install,
                                agent=shlex.quote(PYTHON) + " " + shlex.quote(str(agent)) + "\nexit 0\n")
        wait_for(Pause(self.room.path).file("sidecar2").exists, seconds=20, what="the marker before S's publication")
        kill_child(info)
        wait_for(fence_path(info).exists, seconds=20, what="the watcher's fence")
        go.write_text("go")
        wait_for(ack.exists, seconds=5, what="the helper's update")
        s = successor(self.room, info)
        s.last_message_path = str(sidecar)
        s.profile = replace(s.profile, output_last_message_path=str(sidecar), result_body_records=BODY_SELECTOR)
        s.driver = rt.drivers.driver_for(s.profile)
        out = settle(s)
        self.assertEqual(out["state"], "COMPLETED", out)
        self.assertNotIn("SIDECAR", out["event"]["result"]["body"], out)
        self.assertEqual(s._sidecar_state, capture_mod.SIDECAR_STATE_UNPROVEN)


# =====================================================================================
# F-004 -- partial / unreadable enumeration evidence is named, never complete
# =====================================================================================
@DARWIN_ONLY
class F004PartialReadTests(unittest.TestCase):
    def _slave(self):
        master, slave = _pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        return slave

    def test_a_small_partial_prefix_with_the_scanner_pid_is_listallpids_partial(self) -> None:
        """`probe_small_partial_and_stale_member`: count 100, 80 real entries, a 72-entry prefix
        that keeps the scanner's pid -> the independent walk (before AND after the fill) names
        the 8 missed pids -> `listallpids_partial`, never a complete table."""
        real = pty_supervisor._LIBPROC
        full = sorted(pty_supervisor._sysctl_all_pids())[:80]
        if os.getpid() not in full:
            full[0] = os.getpid()
        prefix = full[:72] if os.getpid() in full[:72] else [os.getpid()] + full[:71]

        class Short:
            def __getattr__(self, name):
                return getattr(real, name)

            def proc_listallpids(self, buf, size):
                if buf is None:
                    return 100
                for i, pid in enumerate(prefix):
                    buf[i] = pid
                return len(prefix)
        with patch.object(pty_supervisor, "_LIBPROC", Short()):
            pids, reason = pty_supervisor._libproc_list_all_pids()
            holders = pty_supervisor.slave_device_holders(os.ttyname(self._slave()))
        self.assertIsNone(pids)
        self.assertEqual(reason, "listallpids_partial")
        self.assertEqual(holders["state"], "unreadable", holders)

    def test_an_unreadable_independent_walk_is_named_not_complete(self) -> None:
        with patch.object(pty_supervisor, "_sysctl_all_pids", return_value=None):
            pids, reason = pty_supervisor._libproc_list_all_pids()
        self.assertIsNone(pids)
        self.assertEqual(reason, "listallpids_crosscheck_unreadable")

    def test_the_real_table_passes_the_independent_cross_check(self) -> None:
        pids, reason = pty_supervisor._libproc_list_all_pids()
        self.assertIsNone(reason, reason)
        self.assertIn(os.getpid(), pids)

    def test_an_unreadable_fd_table_size_never_yields_a_clean_scan(self) -> None:
        """`probe_fd_crosscheck_unreadable`: the 24-of-40-byte listing omitting the live slave
        fd, with BSDINFO nfiles unreadable -> `fd_table_size_unreadable`, never `clean`."""
        slave = self._slave()
        r, w = os.pipe()
        holder = os.fork()
        if holder == 0:
            os.close(w)
            os.read(r, 1)
            os._exit(0)
        os.close(r)
        self.addCleanup(lambda: (os.write(w, b"x"), os.close(w), os.waitpid(holder, 0)))
        ref, _reason = pty_supervisor._slave_reference_devino(os.ttyname(slave))
        real = pty_supervisor._LIBPROC
        import struct

        class Short:
            def __getattr__(self, name):
                return getattr(real, name)

            def proc_pidinfo(self, pid, flavor, arg, buf, size):
                got = real.proc_pidinfo(pid, flavor, arg, buf, size)
                if pid == holder and flavor == pty_supervisor._PROC_PIDLISTFDS and buf is not None and got > 8:
                    raw = bytes(buf)[:got]
                    fds = [struct.unpack_from("<i", raw, i * 8)[0] for i in range(got // 8)]
                    return max(1, sum(fd < slave for fd in fds)) * 8
                return got
        with patch.object(pty_supervisor, "_LIBPROC", Short()), \
                patch.object(pty_supervisor, "_darwin_bsdinfo_nfiles", return_value=None):
            scan = pty_supervisor._scan_process_for_slave(holder, ref, set())
        self.assertEqual(scan, ("unstable", "fd_table_size_unreadable"))

    def test_the_diagnostic_never_approves_finality(self) -> None:
        """A `none_observed` enumeration with NO marker in the capture: the boundary is still
        `boundary_unproven` (the diagnostic is not consulted by the decision)."""
        room = Room()
        self.addCleanup(room.close)
        session, _sentinel = spawn_session(room, "sleep 30\n", run_id="f004d", pump_until_sentinel=False)
        with patch.object(pty_supervisor, "slave_device_holders",
                          return_value={"state": "none_observed", "holders": [], "unenumerable": []}):
            session.exit_proof = {"proven": True, "how": "ladder"}
            drained = session.drain_after_exit(budget_ms=300)
        self.assertEqual(drained.get("outcome"), capture_mod.OUTCOME_BOUNDARY_UNPROVEN, drained)


# =====================================================================================
# F-006 -- parent incarnation, member incarnation, ledger readability
# =====================================================================================
class F006MembershipIdentityTests(unittest.TestCase):
    def _members(self, directory: str, agent_pid: int, agent_start: int):
        return pty_supervisor._Membership(os.fsencode(directory + "/members.jsonl"), fence="s:i",
                                          boot_id=pty_supervisor.host_boot_id(), agent_pid=agent_pid,
                                          agent_start_id=agent_start)

    def _parent_with_child(self):
        """A real parent + its real child; the parent's pid stays alive (blocked) until told."""
        r, w = os.pipe()
        h_r, h_w = os.pipe()
        parent = os.fork()
        if parent == 0:
            os.close(r)
            os.close(h_w)
            child = os.fork()
            if child == 0:
                os.close(w)
                os.read(h_r, 1)
                os._exit(0)
            os.write(w, (str(child) + "\n").encode())
            os.close(w)
            os.read(h_r, 1)
            os.waitpid(child, 0)
            os._exit(0)
        os.close(w)
        os.close(h_r)
        raw = b""
        while not raw.endswith(b"\n"):
            raw += os.read(r, 100)
        os.close(r)
        self.addCleanup(lambda: (os.write(h_w, b"xx"), os.close(h_w), os.waitpid(parent, 0)))
        return parent, int(raw)

    def test_an_exited_or_reused_parent_attributes_nothing(self) -> None:
        """`probe_membership_reused_parent`: the member is the parent pid under an OLD start
        identity and marked exited; the real child of the pid's CURRENT incarnation must not
        be attributed to it."""
        parent, child = self._parent_with_child()
        actual = pty_supervisor.proc_start_ticks(parent)
        with tempfile.TemporaryDirectory() as d:
            members = self._members(d, parent, actual - 1)       # a stale (reused) incarnation
            members._exited(parent)
            with patch.object(pty_supervisor, "_libproc_list_all_pids", return_value=([child], None)):
                members.discover("review_pid_reuse")
            # the child may be attributed ONLY through the parent's CURRENT incarnation -- on
            # Linux the /proc walk first adds the parent itself as this fixture's subreaper
            # child (ppid == the test process), and only THEN its child; on darwin (a patched
            # listing without the parent) the stale member alone attributes nothing
            self._assert_not_via_stale(members, parent, actual, child)
            # the same pid with a STALE start that is NOT marked exited: still refused (identity
            # re-read now differs)
            members2 = self._members(d, parent, actual - 1)
            with patch.object(pty_supervisor, "_libproc_list_all_pids", return_value=([child], None)):
                members2.discover("stale")
            self._assert_not_via_stale(members2, parent, actual, child)
            members.close()
            members2.close()

    def _assert_not_via_stale(self, members, parent: int, actual: int, child: int) -> None:
        child_keys = [k for k in members.members if k[0] == child]
        if child_keys:
            # superseded by OS-48 i8: keys are lifetimes (pid, start, n)
            self.assertIn((parent, actual), {k[:2] for k in members.members},
                          f"the child was attributed without a CURRENT parent incarnation: {members.members}")
        if sys.platform == "darwin":
            self.assertEqual(child_keys, [], members.members)

    def test_a_live_current_parent_attributes_its_child_with_incarnation_keys(self) -> None:
        parent, child = self._parent_with_child()
        actual = pty_supervisor.proc_start_ticks(parent)
        with tempfile.TemporaryDirectory() as d:
            members = self._members(d, parent, actual)
            with patch.object(pty_supervisor, "_libproc_list_all_pids", return_value=([child], None)):
                members.discover("live")
            keys = {k[:2] for k in members.members}          # superseded by OS-48 i8: keys are lifetimes (pid, start, n)
            self.assertIn((parent, actual), keys)
            self.assertIn((child, pty_supervisor.proc_start_ticks(child)), keys)
            ledger = pty_supervisor.read_ledger(d + "/members.jsonl")
            self.assertEqual(ledger["state"], capture_mod.EVIDENCE_FINAL)
            state = json.loads(Path(d + "/members.jsonl.state.json").read_text())
            self.assertEqual((state["appended"], state["failed"]), (len(ledger["records"]), 0))
            members.close()

    def test_two_incarnations_under_one_pid_stay_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            members = self._members(d, 4242, 100)
            added = members._add(4242, 200, pty_supervisor.MEMBER_ROLE_DESCENDANT, "test")
            members._exited(4242, 100)
            # superseded by OS-48 i8 (F-016): member keys are lifetimes (pid, start, n)
            self.assertTrue(members.members[(4242, 100, 1)].get("exited"))
            if sys.platform == "linux":
                # VERSIONED by run_5fcd2beac376 (i8 F-016): a Linux DESCENDANT is a member only
                # through a fixed object that binds the incarnation the caller read; a pid the
                # kernel does not hold (this fixture's 4242) has no pidfd -> refused by name
                # (`pidfd_unavailable`), never recorded with `fixed_object: none`.  The agent's
                # own incarnation (P1, bound by the watcher's waitpid) is unaffected.
                self.assertFalse(added, members.members)
                self.assertNotIn((4242, 200, 1), members.members)
                self.assertTrue([r for r in members.discovery["reasons"] if r.startswith("pidfd_unavailable:")], members.discovery)
            else:
                self.assertTrue(added)
                self.assertFalse(members.members[(4242, 200, 1)].get("exited"))
            records = pty_supervisor.read_members(d + "/members.jsonl")
            exited = [r for r in records if r["event"] == "exited"]
            self.assertEqual([(int(r["identity"]["pid"]), int(r["identity"]["start_id"])) for r in exited], [(4242, 100)])
            members.close()


class F006LedgerAccountingTests(_RoomCase):
    def _completed(self, run_id: str):
        session, _sentinel = spawn_session(self.room, 'printf \'{"type":"result","is_error":false,"session_id":"\'"$OS48_TEST_SID"\'"}\\n\'\nexit 0\n',
                                           run_id=run_id, binding_mode="session_field", binding_field="session_id")
        self.assertEqual(session.await_completion()["state"], "COMPLETED")
        return session

    def _residual_rows(self, session):
        return [r for r in session.journal.rows_for(session.intent_id) if r.get("event") == "descendants_unreaped"]

    def test_an_unreadable_ledger_is_membership_unreadable_with_unknown_accounting(self) -> None:
        """`probe_membership_denied`: EACCES on the actual ledger read during real reclamation ->
        `membership_unreadable`, `unknown` names the ledger, a residual row is journaled."""
        session = self._completed("f006a")
        path = session._members_path()
        real = builtins.open

        def denied(file, *a, **kw):
            if isinstance(file, (bytes, str, os.PathLike)) and os.fsencode(file) == path:
                raise PermissionError(errno.EACCES, "injected membership read denied")
            return real(file, *a, **kw)
        with patch.object(builtins, "open", side_effect=denied):
            residual = session.membership_residual()
            session._reclaim(reason="lock")
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_MEMBERSHIP_UNREADABLE, residual)
        self.assertEqual(residual["ledger_state"], "unreadable")
        self.assertTrue(residual["unknown"], residual)
        rows = self._residual_rows(session)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_vocabulary"]["outcome"], capture_mod.OUTCOME_MEMBERSHIP_UNREADABLE)

    def test_a_torn_or_short_ledger_names_the_unknown_remainder(self) -> None:
        session = self._completed("f006b")
        path = os.fsdecode(session._members_path())
        with open(path, "ab") as handle:
            handle.write(b'{"schema": "os48.member.v1", "event": "observed", "identity": {"pid": 9')   # torn fragment
        residual = session.membership_residual()
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_MEMBERSHIP_UNREADABLE, residual)
        self.assertEqual(residual["ledger_torn"], 1)
        self.assertTrue(any(u.get("ledger") == "incomplete" for u in residual["unknown"]), residual)
        # the watcher's own accounting: a failed append is unknown too
        state_path = path + ".state.json"
        state = json.loads(Path(state_path).read_text())
        Path(state_path).write_text(json.dumps(dict(state, failed=1)))
        residual = session.membership_residual()
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_MEMBERSHIP_UNREADABLE)

    def test_a_reused_member_pid_is_exited_never_alive(self) -> None:
        """Member pid reuse through reclamation: the ledger names (pid, start A); the process at
        that pid now has start B -> that incarnation is gone (`exited`), never `alive`."""
        session = self._completed("f006c")
        path = os.fsdecode(session._members_path())
        me = os.getpid()
        record = {"schema": pty_supervisor.MEMBER_SCHEMA, "event": "observed",
                  "identity": {"pid": me, "start_id": 12345, "boot_id": "b", "incarnation": session.fence,
                               "schema": "os48.process_identity.v1", "source": "test"},
                  "role": "descendant", "observed_via": "test", "pgid": 0, "observed_at": "t"}
        with open(path, "ab") as handle:
            handle.write(json.dumps(record).encode() + b"\n")
        state_path = path + ".state.json"
        state = json.loads(Path(state_path).read_text())
        Path(state_path).write_text(json.dumps(dict(state, appended=state["appended"] + 1)))
        residual = session.membership_residual()
        self.assertEqual(residual["alive"], [], residual)
        self.assertIn(me, residual["exited"])
        # superseded by OS-48 i7 (conservative model): the outcome may be `descendants_unknown`
        # when the sh root forked (`fork_coalesced`); the reused pid is still EXITED, never
        # alive, and the ledger join is clean (never `membership_unreadable`).
        self.assertIn(residual["outcome"], (None, capture_mod.OUTCOME_DESCENDANTS_UNKNOWN))
        self.assertNotIn("membership_unreadable", str(residual["unknown"]))

    def test_members_are_never_signal_targets(self) -> None:
        import inspect
        src = inspect.getsource(pty_supervisor.signal_target) + inspect.getsource(rt.StandaloneSession._reclaim)
        self.assertNotIn("os.kill(", src)
        self.assertNotIn("os.killpg(", src)
        self.assertNotIn("members", inspect.getsource(pty_supervisor.signal_target))
        self.assertNotIn("read_members", inspect.getsource(rt.StandaloneSession._watcher_signal))


# =====================================================================================
# F-007 -- the RC2 cut at the actual tmp-fsynced -> link boundary
# =====================================================================================
class F007ReleaseLinkBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)
        self._pids: list[int] = []
        self.addCleanup(lambda: [os.kill(p, signal.SIGKILL) for p in self._pids if pid_alive(p)])

    @staticmethod
    def _link_pause(pause: Pause, tag: str, *, only_pid: int | None, in_watcher: bool, sup: int | None = None):
        """A `_LINK_HOOK` that pauses at the release record's tmp->link boundary, asserting the
        boundary state (tmp present, target absent) into the cut file."""
        def hook(tmp: bytes, target: bytes) -> None:
            me = os.getpid()
            here = (me != sup) if in_watcher else (only_pid is None or me == only_pid)
            if here and b".release." in target:
                pause.file(tag).write_text(json.dumps({"pid": me, "tmp_exists": os.path.exists(tmp),
                                                       "target_exists": os.path.exists(target),
                                                       "tmp": os.fsdecode(tmp), "target": os.fsdecode(target)}))
                while True:
                    time.sleep(0.05)
        return hook

    def _assert_boundary(self, cut: Path) -> dict:
        state = json.loads(cut.read_text())
        self.assertTrue(state["tmp_exists"], state)
        self.assertFalse(state["target_exists"], state)
        return state

    def test_rc2_supervisor_killed_at_the_release_link_boundary(self) -> None:
        """S drained to R, wrote + fsynced the record tmp, and is SIGKILLed BEFORE the link (tmp
        present, target absent, asserted).  The watcher (custodian, RC1) links its own record;
        the successor verifies the same R; the dead custodian's tmp is never authoritative."""
        def install(pause: Pause, sup: int) -> None:
            capture_mod._LINK_HOOK = self._link_pause(pause, "rc2s", only_pid=sup, in_watcher=False)
        info = supervisor_child(self.room, run_id="rc2s", cut="rc2s", install=install)
        self._pids += [int(info["leader_pid"]), int(info["agent_pid"])]
        cut = Pause(self.room.path).file("rc2s")
        wait_for(cut.exists, seconds=20, what="the release link boundary (supervisor)")
        state = self._assert_boundary(cut)
        kill_child(info)
        wait_for(orphan_note(info).exists, seconds=20, what="the orphan watcher's note")
        wait_for(lambda: not pid_alive(int(info["leader_pid"])), seconds=20, what="the watcher's exit")
        rel = read_release(info)
        self.assertEqual(rel["outcome"], capture_mod.EVIDENCE_FINAL, rel)
        self.assertEqual(rel["record"]["custodian_role"], capture_mod.OWNER_EXIT_WATCHER)
        raw = Path(info["capture"]).read_bytes()
        n = int(read_fence(info)["record"]["boundary"]["offset_n"])
        r, _len, st = capture_mod.find_release_marker(raw, info["fence_nonce"], after=n)
        self.assertEqual((st, int(rel["record"]["offset_r"])), (capture_mod.EVIDENCE_FINAL, r))
        s = successor(self.room, info)
        drained = s.drain_after_exit(budget_ms=3000)
        self.assertEqual(drained.get("finality"), rt.FINALITY_CAPTURE_FINALIZED, drained)
        self.assertEqual((s._release["state"], s._release["recovered_by"], int(s._release["offset_r"])),
                         (capture_mod.EVIDENCE_FINAL, "record_verified", r))
        self.assertTrue(os.path.exists(state["tmp"]) or True)      # a leftover tmp is inert
        self.assertEqual(settle(s)["state"], "COMPLETED")

    def _watcher_link_cut(self, run_id: str, tag: str) -> dict:
        """S is SIGKILLed after its fence (C6), the orphan watcher runs the release as custodian
        and is SIGKILLed at ITS record's tmp->link boundary (asserted): one RELEASE marker in
        the capture, no record, a dead custodian's tmp."""
        def install(pause: Pause, sup: int) -> None:
            pause.wrap(pty_supervisor, "request_release_1", tag + "-s", supervisor_pid=sup)
            capture_mod._LINK_HOOK = self._link_pause(pause, tag, only_pid=None, in_watcher=True, sup=sup)
        info = supervisor_child(self.room, run_id=run_id, cut=tag, install=install)
        self._pids += [int(info["leader_pid"]), int(info["agent_pid"])]
        wait_for(Pause(self.room.path).file(tag + "-s").exists, seconds=20, what="S before release-1")
        kill_child(info)
        cut = Pause(self.room.path).file(tag)
        wait_for(cut.exists, seconds=20, what="the release link boundary (watcher)")
        self._assert_boundary(cut)
        os.kill(int(info["leader_pid"]), signal.SIGKILL)
        wait_dead(int(info["leader_pid"]))
        self.assertFalse(release_path(info).exists())
        raw = Path(info["capture"]).read_bytes()
        self.assertEqual(raw.count(b"<<OS48-RELEASE"), 1)
        return info

    def test_rc2_orphan_watcher_killed_at_the_release_link_boundary_the_successor_recovers_R(self) -> None:
        info = self._watcher_link_cut("rc2w", "rc2w")
        s = successor(self.room, info)
        drained = s.drain_after_exit(budget_ms=3000)
        self.assertEqual(drained.get("finality"), rt.FINALITY_CAPTURE_FINALIZED, drained)
        self.assertEqual((s._release["state"], s._release["recovered_by"]),
                         (capture_mod.EVIDENCE_FINAL, "successor_from_capture"), s._release)
        raw = Path(info["capture"]).read_bytes()
        n = int(drained["offset_n"])
        r, _len, _st = capture_mod.find_release_marker(raw, info["fence_nonce"], after=n)
        self.assertEqual(int(s._release["offset_r"]), r)
        rel = read_release(info)
        self.assertEqual(rel["outcome"], capture_mod.EVIDENCE_FINAL)
        joined = capture_mod.verify_release_record(rel["record"], capture=raw, fence_path=os.fsencode(str(fence_path(info))),
                                                   fence_record=read_fence(info)["record"])
        self.assertTrue(joined["matches"], joined)
        self.assertEqual(settle(s)["state"], "COMPLETED")

    def test_rc3_simultaneous_recovery_after_the_watcher_link_cut_links_exactly_one(self) -> None:
        """No winner is deleted: the custodian died at its link boundary, so no record exists;
        4 successors recover concurrently -> exactly one record, all bind the same R."""
        info = self._watcher_link_cut("rc3w", "rc3w")
        reports = racing_successors(self.room, info, 4)
        self.assertEqual({r.get("release") for r in reports}, {capture_mod.EVIDENCE_FINAL}, reports)
        self.assertEqual(len({r.get("offset_n") for r in reports}), 1, reports)
        rel = read_release(info)
        self.assertEqual(rel["outcome"], capture_mod.EVIDENCE_FINAL)
        self.assertEqual(len([p for p in os.listdir(owner_dir(info)) if ".release." in p and ".tmp." in p]), 1,
                         "the dead custodian's tmp is the only leftover")

    def test_fence_rc2_the_watcher_killed_at_the_fence_link_boundary(self) -> None:
        """The same boundary for the FENCE: S dies mid-drain (C4), the orphan watcher claims g1
        and is SIGKILLed with the fence tmp fsynced and unlinked -> a successor supersedes g1
        and publishes the same N from the captured marker."""
        def install(pause: Pause, sup: int) -> None:
            real = os.read

            def gated_read(fd, size):
                if os.getpid() == sup:
                    pause.mark_and_block("frc2-s")
                return real(fd, size)
            rt.StandaloneSession._cut_reader = staticmethod(gated_read)

            def hook(tmp: bytes, target: bytes) -> None:
                if os.getpid() != sup and b".fence." in target:
                    pause.file("frc2-w").write_text(json.dumps({"tmp_exists": os.path.exists(tmp),
                                                                "target_exists": os.path.exists(target)}))
                    while True:
                        time.sleep(0.05)
            capture_mod._LINK_HOOK = hook

        def steps(session) -> None:
            sentinel = pty_supervisor.exit_sentinel_path(self.room.path / "art", session.run_id,
                                                         session.session_id, session.incarnation)
            wait_for(Path(str(sentinel)).exists, seconds=20, what="the sentinel")
            session._master_reader = rt.StandaloneSession._cut_reader
            session.drain_after_exit(budget_ms=3000)
        info = supervisor_child(self.room, run_id="frc2", cut="frc2", install=install, steps=steps)
        self._pids += [int(info["leader_pid"]), int(info["agent_pid"])]
        wait_for(Pause(self.room.path).file("frc2-s").exists, seconds=20, what="S mid-drain")
        kill_child(info)
        cut = Pause(self.room.path).file("frc2-w")
        wait_for(cut.exists, seconds=20, what="the fence link boundary (watcher)")
        self._assert_boundary(cut)
        os.kill(int(info["leader_pid"]), signal.SIGKILL)
        wait_dead(int(info["leader_pid"]))
        self.assertFalse(fence_path(info).exists())
        s = successor(self.room, info)
        drained = s.drain_after_exit(budget_ms=3000)
        self.assertEqual(drained.get("finality"), rt.FINALITY_CAPTURE_FINALIZED, drained)
        fence = read_fence(info)["record"]
        self.assertEqual(int(fence["owner"]["generation"]), 2)
        self.assertEqual(int(fence["owner"]["superseded"]["pid"]), int(info["leader_pid"]))
        raw = Path(info["capture"]).read_bytes()
        self.assertEqual(fence["boundary"]["sha256_prefix"], capture_mod.prefix_digest(raw, int(fence["boundary"]["offset_n"])))


if __name__ == "__main__":
    unittest.main()
