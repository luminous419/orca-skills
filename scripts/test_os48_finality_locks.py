"""OS-48 finality locks (run_f820764749d6, DESIGN §7 L-01 / L-02 / L-02b / L-03 / L-04 / L-10 /
L-10b / L-12): capture finality is a POSITIVE fact -- the pinned root's reaped exit plus the
in-band fence marker written by the OWNER-HELD slave reference -- and settlement is decided
over ``[baseline, N)`` by the three structural rules (R1 refusal dominance, R2 exactly one
completion record, R3 the dispatch binding).  Every lock drives the PRODUCTION spawn and the
PRODUCTION `StandaloneSession`; adversarial events are ordered by seams (pipes, hooks,
injected returns) -- never by a sleep that has to hit a window.

RED at b9aecce: the module imports OS-48 names (`fence_nonce`, `select_completion`,
`read_capture_fence`, `request_release_1`) that do not exist there; every lock is therefore
red by construction, and the invariants they state (both records inside the boundary; a late
retained-holder byte after N; refusal dominance; a bound single record) are the ones
ANALYSIS F1-F5 showed b9aecce violating (probe_02 / probe_14 / probe_d6b constructions).
"""
from __future__ import annotations

import contextlib
import json
from dataclasses import replace
import os
import pty as _pty
import select
import signal
import socket
import sys
import termios
import threading
import time
import unittest
from pathlib import Path

from scripts.deterministic_workflow import standalone_capture as capture_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_drivers as drivers  # noqa: E402
from scripts.deterministic_workflow import standalone_pty as pty_supervisor  # noqa: E402
from scripts.deterministic_workflow import standalone_runtime as rt  # noqa: E402
from scripts.deterministic_workflow.standalone_profile import profile_from_mapping  # noqa: E402
from scripts.os48_lock_support import (PYTHON, Room, fence_of, release_of,  # noqa: E402
                                       sh_profile, sid_record, spawn_session)

DARWIN_REASON = "OS-48 darwin fact lock: the kernel behaviour it pins is darwin's (probe_01/04/d1/d10)"
DARWIN_ONLY = unittest.skipUnless(sys.platform == "darwin", DARWIN_REASON)

SUCCESS = b'{"type":"result","is_error":false}'
FAILURE = b'{"type":"result","is_error":true}'


def _offsets(raw: bytes, needle: bytes) -> list[int]:
    out, i = [], raw.find(needle)
    while i >= 0:
        out.append(i)
        i = raw.find(needle, i + 1)
    return out


class _RoomCase(unittest.TestCase):
    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)

    def _await_sentinel(self, sentinel: Path, seconds: float = 20.0) -> None:
        deadline = time.time() + seconds
        while not sentinel.exists() and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(sentinel.exists(), "the watcher never wrote the sentinel")

    def _late_bytes(self, session, needle: bytes, seconds: float = 3.0) -> bytes:
        deadline = time.time() + seconds
        while needle not in session.capture.raw() and time.time() < deadline:
            session.pump(timeout_ms=50)
        return session.capture.raw()


# =====================================================================================
# L-01 -- the lost tail: NOTHING written before the root's exit is discarded, however late
# the supervisor reads (the owner-held reference keeps the tty alive)
# =====================================================================================
class L01LostTailTests(_RoomCase):
    def test_a_late_reader_still_finds_both_records_inside_the_boundary(self) -> None:
        """probe_14's construction with the OS-48 spawn: the agent writes a success record
        then a FINAL failure record and exits 0; the supervisor reads NOTHING until the
        watcher's sentinel exists and then holds off longer than darwin's measured discard
        latency (0.5-0.7 s, probe_01/04 -- exceeded, not aimed at).  Both records are inside
        ``[0, N)``, the drain ends at the marker and the settlement is FAILED by R1 (the
        refusal dominates the earlier success) -- never COMPLETED over a truncated capture."""
        session, sentinel = spawn_session(
            self.room, sid_record("result", is_error=False) + sid_record("result", is_error=True)
            + "exit 0\n", run_id="l01", pump_until_sentinel=False)
        self._await_sentinel(sentinel)
        time.sleep(1.0)
        result = session.await_completion()
        drained = session.post_exit_drain
        self.assertEqual(drained.get("ended"), "marker", drained)
        self.assertEqual(drained.get("finality"), rt.FINALITY_CAPTURE_FINALIZED, drained)
        n = int(drained["offset_n"])
        raw = session.capture.raw()
        self.assertIn(SUCCESS[:-1], raw[:n])
        self.assertIn(FAILURE[:-1], raw[:n])
        self.assertEqual(result["state"], "FAILED", result)
        self.assertEqual(result["evidence"].get("provenance_outcome"),
                         capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, result)
        self.assertEqual(fence_of(session)["outcome"], capture_mod.EVIDENCE_FINAL)

    def test_the_read_gate_seam_orders_the_drain_after_the_exit(self) -> None:
        """The same invariant with a READ GATE instead of a hold: the supervisor's master
        reads are blocked on a pipe until the test releases them AFTER the sentinel.  The
        gate is the seam; there is no timing at all."""
        session, sentinel = spawn_session(
            self.room, sid_record("result", is_error=False) + sid_record("result", is_error=True)
            + "exit 0\n", run_id="l01g", pump_until_sentinel=False)
        gate_r, gate_w = os.pipe()
        real = session._master_reader

        def gated(fd: int, size: int) -> bytes:
            os.read(gate_r, 1)                      # blocks until the test releases
            return real(fd, size)
        session._master_reader = gated
        out: dict = {}
        worker = threading.Thread(target=lambda: out.update(result=session.await_completion()))
        worker.start()
        self._await_sentinel(sentinel)
        for _ in range(64):
            os.write(gate_w, b"g")                  # release every read the drain will make
        session._master_reader = real
        worker.join(30)
        self.assertFalse(worker.is_alive(), "await_completion never returned")
        os.close(gate_r)
        os.close(gate_w)
        drained = session.post_exit_drain
        self.assertEqual(drained.get("ended"), "marker", drained)
        n = int(drained["offset_n"])
        raw = session.capture.raw()
        self.assertIn(SUCCESS[:-1], raw[:n])
        self.assertIn(FAILURE[:-1], raw[:n])
        self.assertEqual(out["result"]["state"], "FAILED", out["result"])


# =====================================================================================
# L-02 / L-02b -- settlement over [baseline, N): R1 dominance, R2 exactly one, R3 binding
# =====================================================================================
class L02SettlementRulesTests(unittest.TestCase):
    def _driver(self, mode: str = "session_field", field: str = "session_id", carrier: str = ""):
        room = Room()
        self.addCleanup(room.close)
        profile = sh_profile(str(room.path), binding_mode=mode, binding_field=field,
                             carrier_type=carrier)
        return drivers.driver_for(profile)

    def _select(self, driver, records: list[dict], **kw) -> dict:
        text = "".join(json.dumps(r) + "\n" for r in records)
        return driver.select_completion(text, raw=text.encode(), **kw)

    def test_a_agent_error_then_inherited_helper_success_is_refusal_in_boundary(self) -> None:
        """Reviewer construction (a): agent error record -> a helper's success record -> exit 0
        ⇒ FAILED `refusal_in_boundary` (R1 dominates position)."""
        sel = self._select(self._driver(), [
            {"type": "result", "is_error": True, "session_id": "S"},
            {"type": "result", "is_error": False, "session_id": "S"}], bound_value="S")
        self.assertEqual(sel["outcome"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, sel)
        self.assertEqual(sel["refusal"]["source"], "error_field")

    def test_b_a_lone_helper_success_without_the_binding_is_provenance_unbound(self) -> None:
        sel = self._select(self._driver(), [{"type": "result", "is_error": False}],
                           bound_value="S")
        self.assertEqual(sel["outcome"], capture_mod.OUTCOME_PROVENANCE_UNBOUND, sel)
        self.assertIsNone(sel["record"])
        sel = self._select(self._driver(), [{"type": "result", "is_error": False,
                                             "session_id": "OTHER"}], bound_value="S")
        self.assertEqual(sel["outcome"], capture_mod.OUTCOME_PROVENANCE_UNBOUND, sel)

    def test_c_and_d_a_bound_success_from_any_subtree_member_is_eligible(self) -> None:
        """(c) the agent's own bound success and (d) a shared-session helper's bound success
        inside the boundary are the SAME structural fact (N-002): eligible."""
        for who in ("agent", "helper"):
            with self.subTest(writer=who):
                sel = self._select(self._driver(), [
                    {"type": "result", "is_error": False, "session_id": "S", "by": who}],
                    bound_value="S")
                self.assertIsNone(sel["outcome"], sel)
                self.assertEqual(sel["record"]["by"], who)

    def test_e_a_bound_auth_marker_after_a_success_is_a_refusal(self) -> None:
        """(e) a structured auth-marker refusal AFTER the success record still dominates."""
        driver = self._driver()
        text = (json.dumps({"type": "result", "is_error": False, "session_id": "S"}) + "\n"
                + "Error: Not logged in. Please run /login\n")
        sel = driver.select_completion(text, raw=text.encode(), bound_value="S")
        self.assertEqual(sel["outcome"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, sel)

    def test_two_completion_records_are_provenance_ambiguous(self) -> None:
        sel = self._select(self._driver(), [
            {"type": "result", "is_error": False, "session_id": "S"},
            {"type": "result", "is_error": False, "session_id": "S"}], bound_value="S")
        self.assertEqual(sel["outcome"], capture_mod.OUTCOME_PROVENANCE_AMBIGUOUS, sel)
        self.assertIsNone(sel["record"])

    def test_an_undeclared_binding_mode_is_provenance_unbound(self) -> None:
        """The loader REFUSES a spec without `binding_mode` (ProfileError); a selector built
        with the empty mode in code (a legacy caller) selects nothing: `provenance_unbound`."""
        from scripts.deterministic_workflow.standalone_profile import ProfileError
        with self.assertRaises(ProfileError):
            profile_from_mapping({
                "driver": "claude", "binary": "sh", "supported_range": [[1, 0, 0], [9, 0, 0]],
                "bin_dirs": ["/bin"], "worktree": "/tmp",
                "readiness_records": [{"channel": "structured", "record_type": "system",
                                       "session_field": "session_id"}],
                "completion_records": [{"channel": "structured", "record_type": "result"}],
                "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
                "identity_flag": "--session-id"})
        driver = self._driver()
        legacy = replace(driver.profile, completion_records=(
            replace(driver.profile.completion_records[0], binding_mode="", binding_field=""),))
        driver = drivers.driver_for(legacy)
        sel = self._select(driver, [{"type": "result", "is_error": False, "session_id": "S"}],
                           bound_value="S")
        self.assertEqual(sel["outcome"], capture_mod.OUTCOME_PROVENANCE_UNBOUND, sel)

    def test_an_empty_bound_value_is_provenance_unbound(self) -> None:
        sel = self._select(self._driver(), [
            {"type": "result", "is_error": False, "session_id": ""}], bound_value="")
        self.assertEqual(sel["outcome"], capture_mod.OUTCOME_PROVENANCE_UNBOUND, sel)

    def test_codex_sidecar_binding_needs_the_sidecar_and_the_thread(self) -> None:
        """L-02b: `sidecar_file` -- the runtime-minted sidecar must be present AND the thread
        id on the record or on the most recent carrier before it must equal the bound one;
        different / empty / absent thread or a missing sidecar ⇒ `provenance_unbound`."""
        driver = self._driver(mode="sidecar_file", field="thread_id", carrier="thread.started")
        bound = [{"type": "thread.started", "thread_id": "T1"},
                 {"type": "result", "is_error": False}]
        sel = self._select(driver, bound, bound_value="T1", sidecar_present=True)
        self.assertIsNone(sel["outcome"], sel)
        for name, records, kw in (
                ("different thread", [{"type": "thread.started", "thread_id": "T2"},
                                      {"type": "result", "is_error": False}],
                 dict(bound_value="T1", sidecar_present=True)),
                ("empty thread", [{"type": "thread.started", "thread_id": ""},
                                  {"type": "result", "is_error": False}],
                 dict(bound_value="T1", sidecar_present=True)),
                ("absent carrier", [{"type": "result", "is_error": False}],
                 dict(bound_value="T1", sidecar_present=True)),
                ("missing sidecar", bound, dict(bound_value="T1", sidecar_present=False)),
                ("empty bound", bound, dict(bound_value="", sidecar_present=True))):
            with self.subTest(case=name):
                sel = self._select(driver, records, **kw)
                self.assertEqual(sel["outcome"], capture_mod.OUTCOME_PROVENANCE_UNBOUND, sel)

    def test_the_single_record_optin_binds_nothing_and_says_so(self) -> None:
        sel = self._select(self._driver(mode="single_record_optin", field=""),
                           [{"type": "result", "is_error": False}], bound_value="")
        self.assertIsNone(sel["outcome"], sel)
        self.assertIsNotNone(sel["record"])


class L02PostBoundaryRecordsTests(_RoomCase):
    def test_a_completion_record_after_the_boundary_is_diagnostic_never_settlement(self) -> None:
        """A descendant that keeps the slave writes a FAILURE record after the root exited:
        it lands after N; the settlement over ``[0, N)`` is COMPLETED from the bound success,
        and the late record is retained (readable after N) as a diagnostic."""
        trigger = self.room.path / "l02post.go"
        # HANDSHAKE, not a scheduling sleep (REVIEW_IMPLEMENTATION F-007): the descendant writes
        # only after the test has bound the fence, so its record is post-N by construction.
        session, _sentinel = spawn_session(
            self.room, sid_record("result", is_error=False)
            + "( while [ ! -e %s ]; do sleep 0.01; done; " % trigger
            + sid_record("result", is_error=True).rstrip("\n")
            + "; sleep 3 ) &\nexit 0\n", run_id="l02post")
        result = session.await_completion()
        self.assertEqual(result["state"], "COMPLETED", result)
        n = int(session.post_exit_drain["offset_n"])
        self.assertNotIn(FAILURE[:-1], session.capture.raw()[:n])
        trigger.write_text("go")
        raw = self._late_bytes(session, FAILURE[:-1])
        late = _offsets(raw, FAILURE[:-1])
        self.assertTrue(late, "the retained holder's late record was discarded")
        self.assertGreaterEqual(min(late), n)

    def test_a_real_pty_run_settles_failed_on_a_refusal_before_a_helper_success(self) -> None:
        """Construction (a) end to end over the production spawn."""
        session, _sentinel = spawn_session(
            self.room, sid_record("result", is_error=True) + sid_record("result", is_error=False)
            + "exit 0\n", run_id="l02a")
        result = session.await_completion()
        self.assertEqual(result["state"], "FAILED", result)
        self.assertEqual(result["evidence"].get("provenance_outcome"),
                         capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, result)


# =====================================================================================
# L-03 / L-04 -- SCM_RIGHTS transfer and fork-after-listing: bytes land after N; the
# settlement over [0, N) does not change
# =====================================================================================
class L03ScmRightsTests(_RoomCase):
    def test_a_slave_fd_passed_out_of_the_session_writes_after_the_boundary(self) -> None:
        """The agent hands its slave (fd 1) to an UNRELATED process (this test) over
        SCM_RIGHTS and exits; the receiver writes a failure record only after the fence is
        published.  The transfer is ordered by the socket handshake (a seam), the settlement
        is COMPLETED from the bound success, and the late bytes are after N."""
        sock_path = self.room.path / "xfer.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)
        self.addCleanup(server.close)
        agent = self.room.path / "xfer.py"
        agent.write_text(
            "import os, socket, sys, array, json\n"
            "sys.stdout.write(json.dumps({'type':'result','is_error':False,"
            "'session_id':os.environ['OS48_TEST_SID']})+'\\n'); sys.stdout.flush()\n"
            "s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(%r)\n"
            "s.sendmsg([b'x'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [1]))])\n"
            "s.recv(1)\n"                                   # the receiver acknowledges
            "os._exit(0)\n" % str(sock_path))
        session, _sentinel = spawn_session(
            self.room, "", run_id="l03", argv=[PYTHON, str(agent)], image=PYTHON,
            binding_mode="session_field", binding_field="session_id",
            pump_until_sentinel=False)
        server.settimeout(10)
        conn, _ = server.accept()
        self.addCleanup(conn.close)
        _msg, ancdata, _flags, _addr = conn.recvmsg(1, socket.CMSG_LEN(4))
        fds = []
        for level, kind, data in ancdata:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                fds.extend(int.from_bytes(data[i:i + 4], sys.byteorder) for i in range(0, len(data), 4))
        self.assertEqual(len(fds), 1, "the slave descriptor was not transferred")
        moved = fds[0]
        self.addCleanup(lambda: contextlib.suppress(OSError) and os.close(moved))
        conn.send(b"k")                                     # let the agent exit
        result = session.await_completion()
        self.assertEqual(result["state"], "COMPLETED", result)
        n = int(session.post_exit_drain["offset_n"])
        # The holder OUTSIDE the session writes only now (after the fence): after N.
        wrote = os.write(moved, FAILURE + b"\n")
        self.assertEqual(wrote, len(FAILURE) + 1)
        raw = self._late_bytes(session, FAILURE[:-1])
        late = _offsets(raw, FAILURE[:-1])
        self.assertTrue(late, "the transferred holder's write was discarded")
        self.assertGreaterEqual(min(late), n)
        self.assertEqual(fence_of(session)["outcome"], capture_mod.EVIDENCE_FINAL)


class L04ForkAfterListingTests(_RoomCase):
    def test_a_descendant_forked_at_the_last_moment_cannot_move_the_boundary(self) -> None:
        """probe_02 `fork_after_listing`: the root forks a slave-holding descendant as its
        very last act and exits; the descendant writes later.  No listing is consulted; the
        marker follows the ROOT's reap, so the descendant's bytes are after N and the
        settlement over [0, N) is unchanged (COMPLETED from the bound success)."""
        agent = self.room.path / "forker.py"
        trigger = self.room.path / "l04.go"
        # HANDSHAKE (F-007): the last-moment descendant writes only after the fence is bound.
        agent.write_text(
            "import os, sys, time, json\n"
            "sys.stdout.write(json.dumps({'type':'result','is_error':False,"
            "'session_id':os.environ['OS48_TEST_SID']})+'\\n'); sys.stdout.flush()\n"
            "if os.fork() == 0:\n"
            "    while not os.path.exists(%r): time.sleep(0.005)\n"
            "    os.write(1, b'%s\\n')\n"
            "    time.sleep(3)\n"
            "    os._exit(0)\n"
            "os._exit(0)\n" % (str(trigger), FAILURE.decode()))
        session, _sentinel = spawn_session(
            self.room, "", run_id="l04", argv=[PYTHON, str(agent)], image=PYTHON,
            binding_mode="session_field", binding_field="session_id")
        result = session.await_completion()
        self.assertEqual(result["state"], "COMPLETED", result)
        n = int(session.post_exit_drain["offset_n"])
        trigger.write_text("go")
        raw = self._late_bytes(session, FAILURE[:-1])
        late = _offsets(raw, FAILURE[:-1])
        self.assertTrue(late)
        self.assertGreaterEqual(min(late), n)


# =====================================================================================
# L-10 / L-10b -- the two-phase release retains every acknowledged pre-R byte; the
# release-record cuts are NAMED
# =====================================================================================
class L10ReleaseBoundaryTests(_RoomCase):
    def _positive_ack_session(self, run_id: str):
        """The reviewer's positive-ack construction: a descendant holding the slave writes
        exactly 26 bytes AFTER the marker with a positive `write` return, on a trigger the
        test controls, while the supervisor is NOT reading."""
        trigger = self.room.path / f"{run_id}.go"
        agent = self.room.path / f"{run_id}.py"
        agent.write_text(
            "import os, sys, time, json\n"
            "sys.stdout.write(json.dumps({'type':'result','is_error':False,"
            "'session_id':os.environ['OS48_TEST_SID']})+'\\n'); sys.stdout.flush()\n"
            "if os.fork() == 0:\n"
            "    while not os.path.exists(%r): time.sleep(0.005)\n"
            "    n = os.write(1, b'diagnostic-after-boundary\\n')\n"
            "    open(%r, 'w').write(str(n))\n"
            "    time.sleep(3)\n"
            "    os._exit(0)\n"
            "os._exit(0)\n" % (str(trigger), str(trigger) + ".ack"))
        session, sentinel = spawn_session(
            self.room, "", run_id=run_id, argv=[PYTHON, str(agent)], image=PYTHON,
            binding_mode="session_field", binding_field="session_id")
        return session, trigger

    def test_every_acknowledged_pre_release_byte_is_retained(self) -> None:
        session, trigger = self._positive_ack_session("l10")
        drained = session.drain_after_exit(budget_ms=3000)
        self.assertEqual(drained["ended"], "marker", drained)
        n, mlen = int(drained["offset_n"]), int(drained["marker_len"])
        trigger.write_text("go")
        ack = Path(str(trigger) + ".ack")
        deadline = time.time() + 5
        while not ack.exists() and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(ack.read_text(), "26", "the helper's write was not acknowledged")
        # Nobody has read the master since the marker.  Now the two-phase release.
        released = session._release_two_phase()
        self.assertEqual(released["state"], capture_mod.EVIDENCE_FINAL, released)
        raw = session.capture.raw()
        tail = raw[n + mlen:int(released["offset_r"])]
        # The 26 acknowledged bytes, plus the tty's own ONLCR transport CR (27 on the wire).
        self.assertEqual(tail.replace(b"\r\n", b"\n"), b"diagnostic-after-boundary\n", tail)
        self.assertEqual(int(released["retained_tail_bytes"]), len(tail), released)
        rel = release_of(session)
        self.assertEqual(rel["outcome"], capture_mod.EVIDENCE_FINAL, rel)
        self.assertEqual(int(rel["record"]["retained_tail_bytes"]), len(tail))
        # The authoritative prefix is untouched by the release.
        self.assertEqual(fence_of(session)["record"]["boundary"]["sha256_prefix"],
                         capture_mod.prefix_digest(raw, n))

    def test_the_settlement_is_identical_with_and_without_the_late_write(self) -> None:
        outcomes = []
        for run_id, write_late in (("l10n", False), ("l10y", True)):
            session, trigger = self._positive_ack_session(run_id)
            if write_late:
                trigger.write_text("go")                      # the helper writes after N
                deadline = time.time() + 5
                while not Path(str(trigger) + ".ack").exists() and time.time() < deadline:
                    time.sleep(0.01)
            result = session.await_completion()
            outcomes.append((result["state"], result["evidence"].get("provenance_outcome")))
        self.assertEqual(outcomes[0], outcomes[1], outcomes)
        self.assertEqual(outcomes[0][0], "COMPLETED", outcomes)

    def test_rc4_no_custodian_read_the_release_marker_is_named(self) -> None:
        """RC4: release-1 never reaches the watcher (seam: `request_release_1` refused) ->
        `diagnostic_tail_unaccounted` + `release_record_missing`; the fence and [0, N) are
        untouched."""
        session, _sentinel = spawn_session(self.room, sid_record("result", is_error=False)
                                           + "exit 0\n", run_id="rc4")
        drained = session.drain_after_exit(budget_ms=3000)
        self.assertEqual(drained["ended"], "marker", drained)
        before = Path(os.fsdecode(session._fence_path())).read_bytes()
        real = pty_supervisor.request_release_1
        pty_supervisor.request_release_1 = lambda _s: False
        self.addCleanup(setattr, pty_supervisor, "request_release_1", real)
        released = session._release_two_phase()
        self.assertEqual(released["outcome"], capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED)
        self.assertNotEqual(released["state"], capture_mod.EVIDENCE_FINAL)
        self.assertEqual(release_of(session)["outcome"], "absent")
        self.assertEqual(Path(os.fsdecode(session._fence_path())).read_bytes(), before)

    def test_c9_the_watcher_dying_between_release_1_and_2_keeps_the_record(self) -> None:
        """C9: the RELEASE marker is in the stream and the record linked when the watcher
        dies before release-2 (seam: `_signal_drain_handoff` kills it first): the record
        stays `final`; the post-release read ends on the death's EOF, named in the count."""
        session, _sentinel = spawn_session(self.room, sid_record("result", is_error=False)
                                           + "exit 0\n", run_id="c9")
        drained = session.drain_after_exit(budget_ms=3000)
        self.assertEqual(drained["ended"], "marker", drained)
        real = pty_supervisor._signal_drain_handoff
        leader = int(session.pty["leader_pid"])

        def _kill_then_release(s):
            os.kill(leader, signal.SIGKILL)
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    done, _ = os.waitpid(leader, os.WNOHANG)
                except ChildProcessError:
                    break
                if done == leader:
                    break
                time.sleep(0.01)
            real(s)
        pty_supervisor._signal_drain_handoff = _kill_then_release
        self.addCleanup(setattr, pty_supervisor, "_signal_drain_handoff", real)
        released = session._release_two_phase()
        self.assertEqual(released["state"], capture_mod.EVIDENCE_FINAL, released)
        self.assertEqual(release_of(session)["outcome"], capture_mod.EVIDENCE_FINAL)
        self.assertEqual(fence_of(session)["outcome"], capture_mod.EVIDENCE_FINAL)


# =====================================================================================
# L-12 -- darwin platform facts the design rests on (fail loudly if the host differs)
# =====================================================================================
@DARWIN_ONLY
class L12DarwinFactLocks(unittest.TestCase):
    def _raw_pty(self):
        master, slave = _pty.openpty()
        attrs = termios.tcgetattr(slave)
        attrs[1] &= ~termios.OPOST
        attrs[3] &= ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        self.addCleanup(lambda: contextlib.suppress(OSError) and os.close(master))
        return master, slave

    def test_the_last_slave_close_discards_the_unread_tail(self) -> None:
        """The CONTROL for L-10 (probe_01/04/d10): a single-phase order -- write, close the
        last slave reference, then read -- loses acknowledged bytes on darwin.  This is the
        fact that makes the owner-held reference and the two-phase release necessary."""
        master, slave = self._raw_pty()
        wrote = os.write(slave, b"acknowledged-then-lost\n")
        self.assertEqual(wrote, 23)
        os.close(slave)
        got, end = b"", "timeout"
        deadline = time.time() + 1.5
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
        self.assertNotIn(b"acknowledged-then-lost", got,
                         f"darwin retained the tail after the last slave close ({end}); the "
                         "platform fact L-10 rests on has changed -- re-measure probe_01/04")

    def test_a_held_slave_reference_retains_the_tail_across_a_late_read(self) -> None:
        """probe_d1: with ONE slave reference held, a 1.5 s late read still gets the bytes."""
        master, slave = self._raw_pty()
        os.write(slave, b"retained-while-held\n")
        time.sleep(1.5)
        self.assertTrue(select.select([master], [], [], 0.5)[0])
        self.assertEqual(os.read(master, 65536), b"retained-while-held\n")
        os.close(slave)

    def test_the_marker_survives_onlcr_translation(self) -> None:
        """O-1: a slave with OPOST|ONLCR re-enabled turns the marker's `\\n` into `\\r\\n`;
        `find_marker` / `marker_span` still bind it and record the exact matched length."""
        master, slave = _pty.openpty()
        self.addCleanup(lambda: contextlib.suppress(OSError) and os.close(master))
        nonce = "a" * 32
        os.write(slave, b"x\n" + capture_mod.marker_bytes(nonce))
        data = b""
        while select.select([master], [], [], 0.2)[0]:      # the slave stays HELD (L-12 fact)
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            data += chunk
        os.close(slave)
        self.assertIn(b"\r\n<<OS48-FENCE", data)
        offset_n, marker_len, state = capture_mod.marker_span(data, nonce)
        self.assertEqual(state, capture_mod.EVIDENCE_FINAL, data)
        self.assertEqual(data[offset_n:offset_n + marker_len],
                         b"\r\n<<OS48-FENCE " + nonce.encode() + b">>\r\n")

    def test_kqueue_note_exit_on_a_zombie_is_esrch(self) -> None:
        """The zombie fact the incarnation-bound witness uses: a child that exited but is not
        yet reaped is ESRCH to EVFILT_PROC (no LIVE process) while `kill(pid, 0)` succeeds."""
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        time.sleep(0.2)
        os.kill(pid, 0)                                     # the pid is still held
        kq = select.kqueue()
        try:
            with self.assertRaises(ProcessLookupError):
                kq.control([select.kevent(pid, filter=select.KQ_FILTER_PROC,
                                          flags=select.KQ_EV_ADD, fflags=select.KQ_NOTE_EXIT)],
                           0, 0)
        finally:
            kq.close()
            os.waitpid(pid, 0)
        self.assertEqual(pty_supervisor._pid_presence(pid), "absent")


if __name__ == "__main__":
    unittest.main()
