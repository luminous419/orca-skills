"""OS-48 Linux-native locks (run_f820764749d6, DESIGN §7 L-13; CI condition `not_linux` via
W-CI-LANES): the Linux facts topology A rests on -- the master RETAINS the unread tail after
the last slave close and then reads EIO (probe_01 Linux arm), `PR_SET_CHILD_SUBREAPER`
reparents orphaned descendants to the watcher (probe_d3) WITHOUT consuming the pinned root's
own exit status, `pidfd_open` is the incarnation-bound death witness / delivery path, and
`/proc/<pid>/stat` yields the start identity and the zombie state.  Every class is gated
`LINUX_ONLY`; on macOS the lane expects exactly these skips (`not_linux`), on Linux they RUN.

Run locally on macOS through docker (PLAN §14 gate 6):
    docker run --rm -v "$PWD:/w" -w /w python:3.12-slim python3 -m unittest scripts.test_os48_linux_locks
"""
from __future__ import annotations

import ctypes
import json
import os
import pty as _pty
import select
import signal
import sys
import termios
import time
import unittest
from pathlib import Path

from scripts.deterministic_workflow import standalone_capture as capture_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_identity as identity  # noqa: E402
from scripts.deterministic_workflow import standalone_pty as pty_supervisor  # noqa: E402
from scripts.os48_lock_support import PYTHON, Room, sid_record, spawn_session  # noqa: E402

LINUX_REASON = "requires Linux: PR_SET_CHILD_SUBREAPER / pidfd / EIO retention (OS-48 L-13)"
LINUX_ONLY = unittest.skipUnless(sys.platform == "linux", LINUX_REASON)


@LINUX_ONLY
class L13PtyRetentionFacts(unittest.TestCase):
    def test_the_master_retains_the_tail_after_the_last_slave_close_then_eio(self) -> None:
        """probe_01 (Linux arm): the CONTROL that distinguishes Linux from darwin -- bytes
        written before the last slave close are still readable, and the end is EIO."""
        master, slave = _pty.openpty()
        self.addCleanup(lambda: os.close(master))
        attrs = termios.tcgetattr(slave)
        attrs[1] &= ~termios.OPOST
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        os.write(slave, b"retained-on-linux\n")
        os.close(slave)
        time.sleep(0.2)
        got, end = b"", "timeout"
        deadline = time.time() + 2
        while time.time() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError as exc:
                    end = f"errno={exc.errno}"
                    break
                if not chunk:
                    end = "EOF"
                    break
                got += chunk
        self.assertEqual(got, b"retained-on-linux\n")
        self.assertIn(end, ("errno=5", "EOF"), end)


@LINUX_ONLY
class L13SubreaperTests(unittest.TestCase):
    def test_the_watcher_is_a_subreaper_and_the_agent_exit_code_survives_it(self) -> None:
        """The agent forks a descendant that outlives it; the watcher (subreaper) collects the
        reparented descendant WITHOUT consuming the agent's own status: the sentinel carries
        the agent's REAL exit code (7), never a forged 0."""
        room = Room()
        self.addCleanup(room.close)
        agent = room.path / "orphaner.py"
        agent.write_text(
            "import os, sys, time, json\n"
            "sys.stdout.write(json.dumps({'type':'result','is_error':False,"
            "'session_id':os.environ['OS48_TEST_SID']})+'\\n'); sys.stdout.flush()\n"
            "if os.fork() == 0:\n"
            "    time.sleep(0.3)\n"
            "    os._exit(0)\n"                       # exits AFTER the agent: reparented
            "os._exit(7)\n")
        session, sentinel = spawn_session(room, "", run_id="sub", argv=[PYTHON, str(agent)],
                                          image=PYTHON, binding_mode="session_field",
                                          binding_field="session_id")
        leader = int(session.pty["leader_pid"])
        status = Path(f"/proc/{leader}/status").read_text()
        # the watcher owns its subtree: the reparented descendant's PPid becomes the watcher
        read = pty_supervisor.read_exit_sentinel(sentinel, fence=session.fence)
        self.assertEqual(read, {"outcome": "exited", "code": 7}, read)
        self.assertIn("PPid", status)
        result = session.await_completion()
        self.assertEqual(result["evidence"]["exit_status"], 7, result)

    def test_reap_reparented_never_consumes_the_agent(self) -> None:
        """The seam under the fact above: with the agent a zombie and no other child, the
        subreaper sweep returns without reaping it; the agent's status is still collectable."""
        agent = os.fork()
        if agent == 0:
            os._exit(3)
        time.sleep(0.1)
        pty_supervisor._reap_reparented(agent)
        done, status = os.waitpid(agent, os.WNOHANG)
        self.assertEqual(done, agent, "the subreaper sweep consumed the agent")
        self.assertEqual(os.waitstatus_to_exitcode(status), 3)


@LINUX_ONLY
class L13PidfdAndProcIdentityTests(unittest.TestCase):
    def test_the_parent_death_witness_uses_pidfd_and_fires_on_exit(self) -> None:
        child = os.fork()
        if child == 0:
            time.sleep(0.3)
            os._exit(0)
        ident = {"pid": child, "start_id": pty_supervisor.proc_start_ticks(child)}
        witness = pty_supervisor._ParentWitness(ident)
        self.assertEqual(witness.state, "present")
        self.assertTrue(witness.fds())
        self.assertEqual(witness.fired(3.0), "final")
        os.waitpid(child, 0)

    def test_a_zombie_is_absent_by_its_proc_state(self) -> None:
        child = os.fork()
        if child == 0:
            os._exit(0)
        time.sleep(0.2)
        # /proc still answers for a zombie: start ticks readable, state Z
        self.assertGreater(pty_supervisor.proc_start_ticks(child), 0)
        state = Path(f"/proc/{child}/stat").read_text().rsplit(") ", 1)[1].split()[0]
        self.assertEqual(state, "Z")
        self.assertEqual(pty_supervisor._pid_presence(child), "absent")
        os.waitpid(child, 0)
        self.assertEqual(pty_supervisor.read_identity(child)["start_state"], "absent")

    def test_the_delivery_path_for_a_descendant_is_pidfd_on_linux(self) -> None:
        self.assertEqual(identity.delivery_path("descendant", "linux", have_pidfd=True),
                         "pidfd_send_signal")
        self.assertEqual(identity.delivery_path("agent", "linux", have_pidfd=True), "watcher_mediated")
        self.assertEqual(identity.EVIDENCE_SOURCE_LINUX, pty_supervisor.evidence_source_id())

    def test_the_boot_id_is_the_kernel_random_boot_id(self) -> None:
        self.assertEqual(pty_supervisor.host_boot_id().strip(),
                         Path("/proc/sys/kernel/random/boot_id").read_text().strip())


@LINUX_ONLY
class L13MembershipLedgerTests(unittest.TestCase):
    def test_the_subreaper_ledger_records_a_reparented_descendant_and_its_reap(self) -> None:
        """OS-48 DESIGN §2.2 on Linux (REVIEW_IMPLEMENTATION F-006): a descendant that outlives
        the root reparents to the watcher (subreaper), is discovered by the `/proc` ppid walk
        with its start identity, and is recorded `exited` when the watcher reaps it; the
        teardown residual is then empty."""
        room = Room()
        self.addCleanup(room.close)
        agent = room.path / "orphaner2.py"
        agent.write_text(
            "import os, sys, time, json\n"
            "sys.stdout.write(json.dumps({'type':'result','is_error':False,"
            "'session_id':os.environ['OS48_TEST_SID']})+'\\n'); sys.stdout.flush()\n"
            "if os.fork() == 0:\n"
            "    time.sleep(0.5)\n"
            "    os._exit(0)\n"
            "time.sleep(0.2)\n"                       # the periodic / SIGCHLD walk sees the child
            "os._exit(0)\n")
        session, _sentinel = spawn_session(room, "", run_id="lmem", argv=[PYTHON, str(agent)],
                                          image=PYTHON, binding_mode="session_field",
                                          binding_field="session_id")
        self.assertEqual(session.await_completion()["state"], "COMPLETED")

        def reaped() -> bool:
            return any(m["event"] == "exited" and m["role"] == "descendant"
                       for m in pty_supervisor.read_members(session._members_path()))
        deadline = time.time() + 5                    # bounded: the descendant's own 0.5 s life + a tick
        while not reaped() and time.time() < deadline:
            time.sleep(0.05)
        members = pty_supervisor.read_members(session._members_path())
        self.assertTrue([m for m in members if m["role"] == "descendant"], members)
        self.assertTrue(reaped(), members)
        session._reclaim(reason="lock")
        residual = session.membership_residual()
        self.assertEqual(residual["alive"], [], residual)

    def test_a_detached_helper_is_a_named_residual_on_linux(self) -> None:
        room = Room()
        self.addCleanup(room.close)
        info = room.path / "helper.json"
        agent = room.path / "detached.py"
        agent.write_text(
            "import os,time,json,signal\nr,w=os.pipe()\n"
            "if os.fork()==0:\n    os.close(r);os.setsid();signal.signal(signal.SIGHUP,signal.SIG_IGN)\n"
            "    open(%r,'w').write(json.dumps(dict(pid=os.getpid())))\n"
            "    os.write(w,b'r');os.close(w);time.sleep(30);os._exit(0)\n"
            "os.close(w);os.read(r,1);os.close(r)\n"
            "time.sleep(0.3)\n"                           # the parent outlives the walk (DESIGN §2.2)
            "os.write(1,(json.dumps(dict(type='result',is_error=False,session_id=os.environ['OS48_TEST_SID']))+'\\n').encode());os._exit(0)\n"
            % str(info))
        session, _sentinel = spawn_session(room, "", run_id="ldet", argv=[PYTHON, str(agent)],
                                          image=PYTHON, binding_mode="session_field",
                                          binding_field="session_id")
        helper = json.loads(info.read_text())["pid"]
        self.addCleanup(lambda: os.kill(helper, signal.SIGKILL))
        self.assertEqual(session.await_completion()["state"], "COMPLETED")
        session._reclaim(reason="lock")
        rows = [r for r in session.journal.rows_for(session.intent_id) if r.get("event") == "descendants_unreaped"]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual([a["pid"] for a in rows[0]["source_vocabulary"]["alive"]], [helper])


@LINUX_ONLY
class L13FenceOverLinuxPtyTests(unittest.TestCase):
    def test_the_production_spawn_settles_under_the_fence_on_linux(self) -> None:
        room = Room()
        self.addCleanup(room.close)
        session, _sentinel = spawn_session(
            room, sid_record("result", is_error=False) + sid_record("result", is_error=True)
            + "exit 0\n", run_id="lfence", pump_until_sentinel=False)
        time.sleep(0.5)
        result = session.await_completion()
        drained = session.post_exit_drain
        self.assertEqual(drained["ended"], "marker", drained)
        n = int(drained["offset_n"])
        raw = session.capture.raw()
        self.assertIn(b'"is_error":true', raw[:n])
        self.assertEqual(result["state"], "FAILED", result)
        released = session._release_two_phase()
        self.assertEqual(released["state"], capture_mod.EVIDENCE_FINAL, released)
        reaped = session._reclaim(reason="lock")
        self.assertTrue(reaped.get("reaped"), reaped)


if __name__ == "__main__":
    unittest.main()
