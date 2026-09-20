"""OS-48 execution-ownership locks (run_f820764749d6, DESIGN §7 L-05 / L-11 / L-11b / L-14):
the signal path is bound to an INCARNATION -- the kernel start identity + boot id are
REQUIRED permit axes; delivery to the agent goes through its parent (the exit watcher, whose
single thread both reaps and delivers, so a pre-reap request can never hit a recycled pid);
user-space `killpg` is never an authority (`group_signal_refused`); group teardown is the
kernel's own SIGHUP at controlling-tty revoke.  Kill spies prove that NOTHING reaches the
integer-pid syscalls.

RED at b9aecce: `verify` ignored the start identity (ANALYSIS F10-a: a recycled pid on the
same tty was `owned`), delivery was `os.kill(int)` after a read, and `killpg` was sent to
the recorded pgid (F-003).
"""
from __future__ import annotations

import contextlib
import inspect
import os
import re
import signal
import sys
import time
import unittest
from pathlib import Path

from scripts.deterministic_workflow import standalone_capture as capture_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_identity as identity  # noqa: E402
from scripts.deterministic_workflow import standalone_interrupt as interrupt_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_lifecycle as lifecycle  # noqa: E402
from scripts.deterministic_workflow import standalone_pty as pty_supervisor  # noqa: E402
from scripts.deterministic_workflow import standalone_runtime as rt  # noqa: E402
from scripts.os48_lock_support import KillSpy, PYTHON, Room, sh_profile, spawn_session  # noqa: E402
from scripts.test_os37_pty_supervisor import (BOOT_ID, CHILD_PID, START_ID, TTY,  # noqa: E402
                                              SignalSpy, record, snapshot)

PROD = Path(__file__).resolve().parent.parent / "orca-worker-reviewer-orchestration" / "tools" / "deterministic_workflow"


def _decision(rec, snap):
    return pty_supervisor.check_ownership(rec, snap, staleness_budget_ms=1000, supervisor_pid=999)


def _permit(rec, snap):
    return identity.assert_may_act(rec, "signal",
                                   observed=pty_supervisor.row_for(snap, int(rec["pid"])))


# =====================================================================================
# L-05 -- the identity axes are REQUIRED; a recycled pid is `identity_changed`
# =====================================================================================
class L05IdentityAxesTests(unittest.TestCase):
    def test_a_recycled_pid_on_the_same_tty_is_identity_changed(self) -> None:
        """probe_04 1a: same pid, same tty, same pgid -- a DIFFERENT kernel start identity.
        `verify` is `not_owned: identity_changed`; the gate refuses; no permit exists."""
        rec = record()
        row = {"pid": CHILD_PID, "ppid": 1, "pgid": CHILD_PID, "sid": CHILD_PID, "tty": TTY,
               "stat": "Ss", "start_id": START_ID + 7, "start_state": "final", "boot_id": BOOT_ID}
        verdict = identity.verify(rec, row)
        self.assertEqual((verdict["verdict"], verdict["reason"]),
                         ("not_owned", identity.IDENTITY_CHANGED), verdict)
        with self.assertRaises(identity.OwnershipRefused):
            identity.assert_may_act(rec, "signal", observed=row)

    def test_missing_boot_on_either_side_is_identity_unreadable(self) -> None:
        """F-002: an EMPTY boot id on the record or on the observation is `identity_unreadable`
        -- never a match, never a signal."""
        rec_no_boot = record(boot_id="")
        row = {"pid": CHILD_PID, "ppid": 1, "pgid": CHILD_PID, "sid": CHILD_PID, "tty": TTY,
               "stat": "Ss", "start_id": START_ID, "start_state": "final", "boot_id": BOOT_ID}
        self.assertEqual(identity.verify(rec_no_boot, row)["reason"], identity.IDENTITY_UNREADABLE)
        row_no_boot = dict(row, boot_id="")
        self.assertEqual(identity.verify(record(), row_no_boot)["reason"], identity.IDENTITY_UNREADABLE)
        ok, why = identity.compare_identity({"start_id": START_ID, "boot_id": ""},
                                            START_ID, "final", BOOT_ID)
        self.assertEqual((ok, why), (False, identity.IDENTITY_UNREADABLE))

    def test_an_unreadable_or_absent_start_is_identity_unreadable(self) -> None:
        for state in ("unreadable", "absent", "unknown"):
            with self.subTest(state=state):
                ok, why = identity.compare_identity({"start_id": START_ID, "boot_id": BOOT_ID},
                                                    0, state, BOOT_ID)
                self.assertEqual((ok, why), (False, identity.IDENTITY_UNREADABLE))

    def test_a_changed_boot_id_is_identity_changed(self) -> None:
        ok, why = identity.compare_identity({"start_id": START_ID, "boot_id": "boot-A"},
                                            START_ID, "final", "boot-B")
        self.assertEqual((ok, why), (False, identity.IDENTITY_CHANGED))

    def test_the_boot_id_is_stable_across_processes(self) -> None:
        """The boot id must be the SAME string in every process of one boot, or a successor
        would refuse every ownership check as `identity_changed`.  darwin's `kern.boottime`
        usec field drifts under NTP (measured in this run); the boot-session UUID does not."""
        import subprocess
        code = ("import sys; sys.path.insert(0, '.'); "
                "from scripts.deterministic_workflow import standalone_pty as p; print(p.boot_id())")
        seen = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                               check=True).stdout.strip() for _ in range(3)}
        self.assertEqual(len(seen), 1, seen)
        value = seen.pop()
        self.assertTrue(value)
        self.assertNotIn("usec", value)
        if sys.platform == "darwin":
            uuid = subprocess.run(["sysctl", "-n", "kern.bootsessionuuid"], capture_output=True,
                                  text=True, check=False).stdout.strip()
            if uuid:
                self.assertEqual(value, uuid)

    def test_the_record_carries_the_axes_from_the_spawn(self) -> None:
        """Production `start()`-shaped binding: a real spawn's record carries a non-zero start
        id and the host boot id, and `read_identity` at decision time agrees with it."""
        room = Room()
        self.addCleanup(room.close)
        session, _sentinel = spawn_session(room, "sleep 5\n", run_id="l05", pump_until_sentinel=False)
        rec = session.record
        self.assertGreater(int(rec["proc_start_ticks"]), 0)
        self.assertTrue(rec["boot_id"])
        observed = pty_supervisor.read_identity(int(rec["pid"]))
        self.assertEqual(observed["start_state"], capture_mod.EVIDENCE_FINAL, observed)
        self.assertEqual(int(observed["start_id"]), int(rec["proc_start_ticks"]))
        self.assertEqual(observed["boot_id"], rec["boot_id"])


# =====================================================================================
# L-11 -- the last-read -> delivery cut: NO integer-pid kill; watcher-mediated or refused
# =====================================================================================
class L11DeliveryBindingTests(unittest.TestCase):
    def test_the_agent_is_signalled_only_through_the_watcher(self) -> None:
        rec, snap = record(), snapshot()
        spy = SignalSpy()
        sent = pty_supervisor.signal_target(rec, _decision(rec, snap), 15, permit=_permit(rec, snap),
                                            snapshot=snap, killpg=spy.send_group,
                                            kill=spy.send_one, watcher=spy.via_watcher)
        self.assertEqual(spy.kill, [], "an integer-pid kill was sent")
        self.assertEqual(spy.killpg, [], "a user-space killpg was sent")
        self.assertEqual(spy.watcher, [15])
        rungs = {s["rung"]: s["result"] for s in sent["sent"]}
        self.assertEqual(rungs["agent_via_watcher"], "sent")
        self.assertEqual(rungs["descendant_groups"], "withheld:" + identity.GROUP_SIGNAL_REFUSED)

    def test_a_pid_reused_after_the_permit_is_refused_by_the_watcher_not_killed(self) -> None:
        """probe_d7b's cut: the permit was granted, then the target was reaped and its pid
        reused BEFORE delivery.  The watcher (which reaped it) answers `signal_target_reaped`;
        the kill spy must record NOTHING -- a mutation that delivers `os.kill(int)` here would
        hit the stranger and fail this lock."""
        rec, snap = record(), snapshot()
        spy = SignalSpy()
        permit = _permit(rec, snap)
        # the cut: the incarnation is gone; the watcher already reaped it
        sent = pty_supervisor.signal_target(
            rec, _decision(rec, snap), 15, permit=permit, snapshot=snap,
            killpg=spy.send_group, kill=spy.send_one,
            watcher=lambda sig: "refused:" + identity.SIGNAL_TARGET_REAPED)
        self.assertEqual(spy.kill, [])
        self.assertEqual(spy.killpg, [])
        rungs = {s["rung"]: s["result"] for s in sent["sent"]}
        self.assertEqual(rungs["agent_via_watcher"], "refused:" + identity.SIGNAL_TARGET_REAPED)

    def test_no_watcher_and_no_pidfd_is_signal_unbound(self) -> None:
        """darwin non-child: no atomic signal primitive exists -> `signal_unbound`, nothing sent."""
        rec, snap = record(), snapshot()
        spy = SignalSpy()
        sent = pty_supervisor.signal_target(rec, _decision(rec, snap), 15, permit=_permit(rec, snap),
                                            snapshot=snap, killpg=spy.send_group, kill=spy.send_one)
        self.assertEqual(spy.total, 0)
        rungs = {s["rung"]: s["result"] for s in sent["sent"]}
        self.assertEqual(rungs["agent_via_watcher"], "refused:" + identity.SIGNAL_UNBOUND)
        self.assertEqual(identity.delivery_path("descendant", "darwin", have_pidfd=False),
                         "refuse:" + identity.SIGNAL_UNBOUND)

    def test_may_killpg_always_refuses(self) -> None:
        for args in ((1,), (4242, [4242]), (4242, [4242], [4242, 102])):
            with self.subTest(args=args):
                self.assertEqual(identity.may_killpg(*args), (False, identity.GROUP_SIGNAL_REFUSED))

    def test_the_real_watcher_delivers_before_the_reap_and_refuses_after(self) -> None:
        """Over the production spawn: `request_watcher_signal` -> `sent` while the agent lives
        (and the agent dies of it); after the reap the same request is refused
        `signal_target_reaped`; a foreign fence is `signal_unbound`."""
        room = Room()
        self.addCleanup(room.close)
        session, sentinel = spawn_session(room, "sleep 30\n", run_id="l11real",
                                          pump_until_sentinel=False)
        ctl = int(session.pty["control_fd"])
        self.assertEqual(pty_supervisor.request_watcher_signal(ctl, signal.SIGTERM, "other:fence"),
                         "refused:" + identity.SIGNAL_UNBOUND)
        self.assertEqual(pty_supervisor.request_watcher_signal(ctl, signal.SIGTERM, session.fence),
                         "sent")
        deadline = time.time() + 10
        while not Path(str(sentinel)).exists() and time.time() < deadline:
            session.pump(timeout_ms=20)
        self.assertTrue(Path(str(sentinel)).exists(), "the agent did not die of the watcher's signal")
        read = pty_supervisor.read_exit_sentinel(sentinel, fence=session.fence)
        self.assertEqual(read["code"], 128 + signal.SIGTERM, read)
        self.assertEqual(pty_supervisor.request_watcher_signal(ctl, signal.SIGKILL, session.fence),
                         "refused:" + identity.SIGNAL_TARGET_REAPED)

    def test_the_interrupt_ladder_sends_no_integer_pid_kill(self) -> None:
        """The production ladder over an injected table with kill spies: every delivery goes
        to the watcher seam; `kill`/`killpg` receive nothing across all rungs."""
        room = Room()
        self.addCleanup(room.close)
        profile = sh_profile(str(room.path))
        spy = KillSpy()
        via: list[int] = []
        rows = [snapshot()["rows"][0]]

        def table(_tty):
            return {"tty": TTY, "captured_at": time.time(), "rows": tuple(rows), "readable": True}

        def watcher(sig):
            via.append(sig)
            rows.clear()                       # the agent is gone after the first delivery
            return "sent"
        result = interrupt_mod.interrupt(
            "i-l11", "stop", record=record(), profile=profile, table_reader=table,
            supervisor_pid=999, killpg=spy.send_group, kill=spy.send_one,
            sleep=lambda _s: None, watcher=watcher,
            identity_reader=lambda pid: {"start_id": START_ID, "start_state": "final",
                                         "boot_id": BOOT_ID})
        self.assertEqual(spy.kill, [], result)
        self.assertEqual(spy.killpg, [], result)
        self.assertTrue(via, result)


# =====================================================================================
# L-11b -- group teardown is the KERNEL's SIGHUP at revoke; no user-space group signal
# =====================================================================================
class L11bGroupTeardownTests(unittest.TestCase):
    def test_a_same_group_helper_receives_the_kernel_sighup_not_a_user_space_signal(self) -> None:
        """probe_d8's construction: the agent forks a same-group helper BEFORE any discovery
        and exits.  No user-space signal is ever sent to the helper (os.kill / os.killpg are
        spied for the whole teardown); when the session leader (the watcher) exits after the
        release, the kernel's own SIGHUP to the foreground group ends the helper."""
        room = Room()
        self.addCleanup(room.close)
        pidfile = room.path / "helper.pid"
        agent = room.path / "grp.py"
        agent.write_text(
            "import os, sys, time, json\n"
            "sys.stdout.write(json.dumps({'type':'result','is_error':False,"
            "'session_id':os.environ['OS48_TEST_SID']})+'\\n'); sys.stdout.flush()\n"
            "if os.fork() == 0:\n"
            "    open(%r,'w').write(str(os.getpid()))\n"
            "    time.sleep(30)\n"                     # default SIGHUP disposition: dies of it
            "    os._exit(0)\n"
            "os._exit(0)\n" % str(pidfile))
        sent: list[tuple] = []
        real_kill, real_killpg = os.kill, os.killpg

        def spy_kill(pid, sig):
            if sig != 0:
                sent.append(("kill", pid, sig))
            return real_kill(pid, sig)

        def spy_killpg(pgid, sig):
            sent.append(("killpg", pgid, sig))
            raise AssertionError("os.killpg was called during teardown")
        os.kill, os.killpg = spy_kill, spy_killpg
        self.addCleanup(setattr, os, "kill", real_kill)
        self.addCleanup(setattr, os, "killpg", real_killpg)
        session, _sentinel = spawn_session(room, "", run_id="l11b", argv=[PYTHON, str(agent)],
                                           image=PYTHON, binding_mode="session_field",
                                           binding_field="session_id")
        deadline = time.time() + 5
        while not pidfile.exists() and time.time() < deadline:
            time.sleep(0.01)
        helper = int(pidfile.read_text())
        self.assertEqual(os.getpgid(helper), int(session.record["pgid"]))
        result = session.await_completion()
        self.assertEqual(result["state"], "COMPLETED", result)
        reclaimed = session._reclaim(reason="lock")
        self.assertTrue(reclaimed.get("reaped"), reclaimed)
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                real_kill(helper, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        with self.assertRaises(ProcessLookupError, msg="the kernel SIGHUP did not reach the helper"):
            real_kill(helper, 0)
        self.assertEqual([s for s in sent if s[1] == helper], [],
                         "a user-space signal was sent to the helper")
        self.assertEqual([s for s in sent if s[0] == "killpg"], [])


# =====================================================================================
# L-14 -- mutation / static locks: decisions never reach the enumeration or os.killpg
# =====================================================================================
class L14StaticInvariantTests(unittest.TestCase):
    DECISION_MODULES = ("standalone_runtime.py", "standalone_interrupt.py",
                        "standalone_identity.py", "standalone_lifecycle.py",
                        "standalone_drivers.py")

    def _source(self, name: str) -> str:
        return (PROD / name).read_text(encoding="utf-8")

    def test_decision_modules_call_no_enumeration_and_no_killpg(self) -> None:
        forbidden = re.compile(r"\b(os\.killpg|slave_device_holders|_slave_holders|"
                               r"_slave_holder_state|_libproc_list_all_pids|proc_listallpids)\s*\(")
        for name in self.DECISION_MODULES:
            with self.subTest(module=name):
                code = "\n".join(line for line in self._source(name).splitlines()
                                 if not line.lstrip().startswith("#"))
                self.assertIsNone(forbidden.search(code),
                                  f"{name} reaches an enumeration / group-signal function")

    def test_the_pty_module_never_sends_a_user_space_killpg(self) -> None:
        """The only `killpg` call in the pty module is the DIAGNOSTIC probe's `killpg(pgid, 0)`
        membership check behind a seam (L-11 guard); no delivery path calls `os.killpg`."""
        src = "\n".join(line for line in self._source("standalone_pty.py").splitlines()
                        if not line.lstrip().startswith("#"))
        calls = [m.start() for m in re.finditer(r"(?<![\w.`])killpg\(", src)]
        self.assertTrue(calls)
        for pos in calls:
            line = src[src.rfind("\n", 0, pos) + 1:src.find("\n", pos)]
            self.assertIn("killpg(pgid, 0)", line, line)
        self.assertNotRegex(src, r"os\.killpg\((?!\s*\)|pgid, 0)")
        sig = inspect.getsource(pty_supervisor.signal_target)
        self.assertNotIn("os.kill(", sig)
        self.assertNotIn("killpg(", sig.replace("may_killpg(", ""))

    def test_the_lost_reasons_carry_every_named_outcome(self) -> None:
        for name in ("boundary_unproven", "fence_missing", "fence_mismatch", "fence_foreign",
                     "legacy_finalized_record", "owner_conflict", "finalizer_alive",
                     "exit_unproven", "identity_unreadable", "identity_changed",
                     "evidence_inconsistent", "provenance_ambiguous", "provenance_unbound",
                     "signal_unbound", "signal_target_reaped", "group_signal_refused",
                     "fence_published_no_claim", "succession_unwitnessed"):
            with self.subTest(name=name):
                self.assertIn(name, lifecycle.LOST_REASONS)
                self.assertIn(name, lifecycle.OS48_LOST_OUTCOMES)
                resolved = lifecycle.resolve_unknown("os48_named", lost_reason=name)
                self.assertEqual((resolved["state"], resolved["lost_reason"]), ("LOST", name))

    def test_the_legacy_finalized_schema_is_refused_by_name_everywhere(self) -> None:
        self.assertFalse(hasattr(capture_mod, "write_capture_finalized"))
        self.assertFalse(hasattr(capture_mod, "read_capture_finalized"))
        self.assertFalse(hasattr(capture_mod, "finalized_matches"))
        self.assertEqual(capture_mod.LEGACY_FINALIZED_SCHEMA, "os37.capture_finalized.v1")
        self.assertEqual(capture_mod.CAPTURE_FENCE_SCHEMA, "os48.capture_fence.v1")

    def test_wiring_the_enumeration_into_the_boundary_is_caught(self) -> None:
        """Mutation (a): a boundary decided by `slave_device_holders() == 'none_observed'`
        instead of the marker -- the settlement lock must FAIL.  Driven by replacing
        `marker_span` with a scan-based answer and running the positive path: the mutated
        decision claims a boundary the capture does not hold, and `fence_matches` refuses it
        (`capture_digest_mismatch`/offset), so no settlement can be COMPLETED from it."""
        room = Room()
        self.addCleanup(room.close)
        session, _sentinel = spawn_session(room, "printf 'x\\n'\nexit 0\n", run_id="l14a")
        real = capture_mod.marker_span

        def mutated(data, nonce):
            # "no holder observed" read as the boundary at the current end of the capture
            return len(data), 0, capture_mod.EVIDENCE_FINAL
        capture_mod.marker_span = mutated
        self.addCleanup(setattr, capture_mod, "marker_span", real)
        drained = session.drain_after_exit(budget_ms=2000)
        capture_mod.marker_span = real
        fence = capture_mod.read_capture_fence(session._fence_path(), fence=session.fence)
        if fence["outcome"] == capture_mod.EVIDENCE_FINAL:
            offset_n = int(fence["record"]["boundary"]["offset_n"])
            real_n, _len, state = real(session.capture.raw(), session.fence_nonce)
            self.assertNotEqual((offset_n, 0), (real_n, 0),
                                "the mutated boundary coincides with the marker; mutation not caught")
        # Whatever the mutant published, the marker-based reading disagrees: the lock fails.
        real_n, _len, state = real(session.capture.raw(), session.fence_nonce)
        self.assertEqual(state, capture_mod.EVIDENCE_FINAL)
        self.assertNotEqual(int(drained.get("offset_n", -1)), real_n)


if __name__ == "__main__":
    unittest.main()
