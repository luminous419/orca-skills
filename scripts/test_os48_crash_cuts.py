"""OS-48 REAL crash cuts (run_f820764749d6, REVIEW_IMPLEMENTATION F-007 / DESIGN §1.7 C1-C9 +
RC1-RC3): every cut is an ACTUAL SIGKILL of a real supervisor or a real exit watcher at a
deterministic pause seam (an acknowledgment file, never a scheduling sleep), followed by a
RELOAD through the production masterless successor (`drain_after_exit` -> `_fence_from_disk`
-> release recovery -> `await_completion` -> `_settle`), and, where the design names it, by
CONCURRENT successors racing the same records.  The assertion is always the DESIGN outcome:
the same N / prefix digest as the live path, or the named non-success -- never COMPLETED
over an unverified boundary.

RED at b9aecce: the harness imports OS-48 names that do not exist there (every test errors);
the invariants (C5 witness-bound succession, C8/RC1 single RELEASE marker, RC2/RC3 release
recovery, C2 unknown exit code) are the ones REVIEW_IMPLEMENTATION F-002/F-003/F-007 showed
the i1 tree violating (`evidence/review_implementation_i1/probe_release_cut_real.py`,
`probe_real_contracts.py`).
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import time
import unittest
from pathlib import Path

from scripts.deterministic_workflow import standalone_capture as capture_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_pty as pty_supervisor  # noqa: E402
from scripts.deterministic_workflow import standalone_runtime as rt  # noqa: E402
from scripts.os48_cut_harness import (SUCCESS, Pause, fence_path, kill_child, orphan_note,  # noqa: E402
                                      owner_dir, pid_alive, racing_successors, read_fence,
                                      read_release, release_path, settle, successor,
                                      supervisor_child, wait_dead, wait_for)
from scripts.os48_lock_support import Room, spawn_session  # noqa: E402


def _same_proof(test: unittest.TestCase, info: dict, drained: dict) -> None:
    """The successor's bound fence equals the record on disk and the recomputed prefix digest."""
    test.assertEqual(drained.get("finality"), rt.FINALITY_CAPTURE_FINALIZED, drained)
    fence = read_fence(info)
    test.assertEqual(fence["outcome"], capture_mod.EVIDENCE_FINAL, fence)
    n = int(fence["record"]["boundary"]["offset_n"])
    raw = Path(info["capture"]).read_bytes()
    test.assertEqual(int(drained["offset_n"]), n)
    test.assertEqual(fence["record"]["boundary"]["sha256_prefix"], capture_mod.prefix_digest(raw, n))
    test.assertIn(b'"is_error":false', raw[:n])


class _CutCase(unittest.TestCase):
    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)
        self._pids: list[int] = []

    def tearDown(self) -> None:
        for pid in self._pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def _track(self, info: dict) -> None:
        self._pids += [int(info["leader_pid"]), int(info["agent_pid"])]

    def _await_watcher_done(self, info: dict, seconds: float = 20.0) -> dict:
        note = orphan_note(info)
        wait_for(note.exists, seconds=seconds, what="the orphan watcher's note")
        wait_for(lambda: not pid_alive(int(info["leader_pid"])), seconds=seconds, what="the watcher's exit")
        return json.loads(note.read_text())


# =====================================================================================
# Supervisor-death cuts: C4, C5, C6, C8 (=RC1), RC2 -- the watcher / a successor finishes
# =====================================================================================
class SupervisorDeathCuts(_CutCase):
    def test_c4_supervisor_killed_mid_drain_the_watcher_publishes_the_same_proof(self) -> None:
        """C4: S is SIGKILLed while paused INSIDE `drain_after_exit` (its first master read
        after the sentinel).  W: guard EOF + witness FINAL -> claims g1, publishes; the
        successor binds the SAME N / digest and settles COMPLETED."""
        def install(pause: Pause, sup: int) -> None:
            real = os.read

            def gated_read(fd, size):
                if os.getpid() == sup:
                    pause.mark_and_block("c4")          # the drain's FIRST master read
                return real(fd, size)
            rt.StandaloneSession._cut_reader = staticmethod(gated_read)

        def steps(session) -> None:
            # no pump: the marker stays in the master buffer (the watcher holds the slave), so
            # the drain's first read is where S dies -- genuinely mid-drain
            sentinel = pty_supervisor.exit_sentinel_path(self.room.path / "art", session.run_id,
                                                         session.session_id, session.incarnation)
            wait_for(Path(str(sentinel)).exists, seconds=20, what="the sentinel")
            session._master_reader = rt.StandaloneSession._cut_reader
            session.drain_after_exit(budget_ms=3000)
        info = supervisor_child(self.room, run_id="c4", cut="c4", install=install, steps=steps)
        self._track(info)
        wait_for(Pause(self.room.path).file("c4").exists, seconds=20, what="cut c4")
        self.assertFalse(fence_path(info).exists(), "the fence existed before the cut")
        kill_child(info)
        note = self._await_watcher_done(info)
        self.assertEqual(note.get("role"), "finalizer", note)
        self.assertEqual(note.get("witness"), "final", note)
        fence = read_fence(info)
        self.assertEqual(fence["record"]["owner"]["owner_role"], capture_mod.OWNER_EXIT_WATCHER)
        self.assertEqual(int(fence["record"]["owner"]["generation"]), 1)
        s = successor(self.room, info)
        drained = s.drain_after_exit(budget_ms=3000)
        _same_proof(self, info, drained)
        self.assertEqual(read_release(info)["outcome"], capture_mod.EVIDENCE_FINAL)
        self.assertEqual(settle(s)["state"], "COMPLETED")

    def test_c5_supervisor_killed_after_claim_before_fence_the_watcher_supersedes_g1(self) -> None:
        """C5: S claimed g1 and is SIGKILLed paused before `write_capture_fence`.  W's witness is
        for exactly S (pinned pid + start id) -> g2 {superseded: S}, fence names g2; the
        successor binds it and settles COMPLETED; no g3 ever."""
        def install(pause: Pause, sup: int) -> None:
            pause.wrap(capture_mod, "write_capture_fence", "c5", supervisor_pid=sup)
        info = supervisor_child(self.room, run_id="c5", cut="c5", install=install)
        self._track(info)
        wait_for(Pause(self.room.path).file("c5").exists, seconds=20, what="cut c5")
        highest, g1, _ = capture_mod.read_generations(owner_dir(info), info["incarnation"])
        self.assertEqual(highest, 1)
        self.assertEqual(int(g1["owner"]["pid"]), int(info["supervisor_pid"]))
        kill_child(info)
        note = self._await_watcher_done(info)
        self.assertEqual(note.get("role"), "finalizer", note)
        fence = read_fence(info)
        owner = fence["record"]["owner"]
        self.assertEqual(int(owner["generation"]), 2, owner)
        self.assertEqual(int(owner["superseded"]["pid"]), int(info["supervisor_pid"]))
        self.assertEqual(int(owner["superseded"]["start_id"]), int(g1["owner"]["start_id"]))
        self.assertIn(owner["death_evidence"], ("note_exit_pinned", "pidfd_readable"))
        self.assertEqual(capture_mod.read_generations(owner_dir(info), info["incarnation"])[0], 2)
        s = successor(self.room, info)
        _same_proof(self, info, s.drain_after_exit(budget_ms=3000))
        self.assertEqual(settle(s)["state"], "COMPLETED")
        self.assertEqual(capture_mod.read_generations(owner_dir(info), info["incarnation"])[0], 2, "a g3 appeared")

    def test_c6_supervisor_killed_after_fence_before_release_the_watcher_is_custodian(self) -> None:
        """C6: S published the fence (g1) and is SIGKILLed paused before release-1.  W: fence
        first -> NO claim (g1 stays), performs the release as custodian: RELEASE marker, R,
        `release.<inc>` (custodian_role exit_watcher); the successor verifies both."""
        def install(pause: Pause, sup: int) -> None:
            pause.wrap(pty_supervisor, "request_release_1", "c6", supervisor_pid=sup)
        info = supervisor_child(self.room, run_id="c6", cut="c6", install=install)
        self._track(info)
        wait_for(Pause(self.room.path).file("c6").exists, seconds=20, what="cut c6")
        before = fence_path(info).read_bytes()
        kill_child(info)
        note = self._await_watcher_done(info)
        self.assertEqual(note.get("role"), "custodian", note)
        self.assertEqual(capture_mod.read_generations(owner_dir(info), info["incarnation"])[0], 1)
        self.assertEqual(fence_path(info).read_bytes(), before, "the fence was rewritten")
        rel = read_release(info)
        self.assertEqual(rel["outcome"], capture_mod.EVIDENCE_FINAL, rel)
        self.assertEqual(rel["record"]["custodian_role"], capture_mod.OWNER_EXIT_WATCHER)
        s = successor(self.room, info)
        drained = s.drain_after_exit(budget_ms=3000)
        _same_proof(self, info, drained)
        self.assertEqual(s._release["state"], capture_mod.EVIDENCE_FINAL, s._release)
        self.assertEqual(s._release["recovered_by"], "record_verified")

    def test_c8_rc1_supervisor_killed_after_release_1_before_R_the_marker_is_reused(self) -> None:
        """C8 / RC1 (the reviewer's `probe_release_cut_real`): S requested release-1, the RELEASE
        marker is in the stream, S is SIGKILLed paused before it reads R.  W: guard EOF ->
        orphan: REUSES the marker (exactly ONE in the capture), drains to R, links the record;
        the successor verifies it joined to the fence."""
        def install(pause: Pause, sup: int) -> None:
            pause.wrap(capture_mod, "find_release_marker", "c8", supervisor_pid=sup)

        def steps(session) -> None:
            deadline = time.time() + 20
            sentinel = pty_supervisor.exit_sentinel_path(self.room.path / "art", session.run_id,
                                                         session.session_id, session.incarnation)
            while not Path(str(sentinel)).exists() and time.time() < deadline:
                session.pump(timeout_ms=20)
            session.drain_after_exit(budget_ms=3000)
            session._release_two_phase()               # pauses at its first find_release_marker
        info = supervisor_child(self.room, run_id="c8", cut="c8", install=install, steps=steps)
        self._track(info)
        wait_for(Pause(self.room.path).file("c8").exists, seconds=20, what="cut c8")
        kill_child(info)
        note = self._await_watcher_done(info)
        self.assertTrue(note.get("release_marker_reused"), note)
        raw = Path(info["capture"]).read_bytes()
        self.assertEqual(raw.count(b"<<OS48-RELEASE"), 1, "a second RELEASE marker was written")
        rel = read_release(info)
        self.assertEqual(rel["outcome"], capture_mod.EVIDENCE_FINAL, rel)
        self.assertEqual(rel["record"]["custodian_role"], capture_mod.OWNER_EXIT_WATCHER)
        s = successor(self.room, info)
        drained = s.drain_after_exit(budget_ms=3000)
        _same_proof(self, info, drained)
        self.assertEqual(s._release["state"], capture_mod.EVIDENCE_FINAL, s._release)
        self.assertEqual(int(s._release["offset_r"]), int(rel["record"]["offset_r"]))

    def test_rc2_custodian_killed_after_record_tmp_before_link_the_successor_recovers_R(self) -> None:
        """RC2 (REVIEW_IMPLEMENTATION_iteration2 F-007: the pause is AT the boundary): S drained to
        R, wrote + fsynced the record tmp and is SIGKILLed before `os.link` -- the seam is
        `standalone_capture._LINK_HOOK`, and the cut file records that the tmp exists and the
        target does not.  The orphan watcher (RC1) links its own record; the successor
        verifies the same R.  The watcher-path cut and the simultaneous recovery live in
        `test_os48_review_i2_locks.F007ReleaseLinkBoundaryTests`."""
        def install(pause: Pause, sup: int) -> None:
            def hook(tmp: bytes, target: bytes) -> None:
                if os.getpid() == sup and b".release." in target:
                    pause.file("rc2").write_text(json.dumps({"tmp_exists": os.path.exists(tmp),
                                                             "target_exists": os.path.exists(target)}))
                    while True:
                        time.sleep(0.05)
            capture_mod._LINK_HOOK = hook
        info = supervisor_child(self.room, run_id="rc2", cut="rc2", install=install)
        self._track(info)
        cut = Pause(self.room.path).file("rc2")
        wait_for(cut.exists, seconds=20, what="cut rc2 (the tmp->link boundary)")
        state = json.loads(cut.read_text())
        self.assertEqual((state["tmp_exists"], state["target_exists"]), (True, False), state)
        kill_child(info)
        self._await_watcher_done(info)
        raw = Path(info["capture"]).read_bytes()
        self.assertEqual(raw.count(b"<<OS48-RELEASE"), 1)
        s = successor(self.room, info)
        drained = s.drain_after_exit(budget_ms=3000)
        _same_proof(self, info, drained)
        self.assertEqual(s._release["state"], capture_mod.EVIDENCE_FINAL, s._release)
        self.assertEqual(s._release["recovered_by"], "record_verified")
        rel = read_release(info)
        n = int(drained["offset_n"])
        r, _len, _st = capture_mod.find_release_marker(raw, info["fence_nonce"], after=n)
        self.assertEqual((int(rel["record"]["offset_r"]), int(s._release["offset_r"])), (r, r))

    def test_rc3_two_custodians_race_the_release_link(self) -> None:
        """RC3 without manufacturing absence: the LIVE supervisor and 3 successors race the
        release link at the same time (the supervisor is paused at its tmp->link boundary
        while the successors recover from the captured marker, then released): exactly one
        record on disk, every party binds the same R, no winner is ever deleted."""
        resume = self.room.path / "rc3.resume"

        def install(pause: Pause, sup: int) -> None:
            def hook(tmp: bytes, target: bytes) -> None:
                if os.getpid() == sup and b".release." in target:
                    pause.file("rc3").write_text("1")
                    while not resume.exists():
                        time.sleep(0.02)
            capture_mod._LINK_HOOK = hook
        info = supervisor_child(self.room, run_id="rc3", cut="rc3", install=install)
        self._track(info)
        wait_for(Pause(self.room.path).file("rc3").exists, seconds=20, what="cut rc3")
        # 3 successors recover concurrently while the supervisor holds its fsynced tmp ...
        import threading
        reports: list = []
        racer = threading.Thread(target=lambda: reports.extend(racing_successors(self.room, info, 3)))
        racer.start()
        time.sleep(0.05)
        resume.write_text("go")                       # ... and the supervisor links at once
        racer.join(60)
        wait_for(lambda: (self.room.path / f"info.{info['run_id']}.done").exists(), seconds=20, what="the supervisor's release")
        kill_child(info)
        self.assertEqual({r.get("release") for r in reports}, {capture_mod.EVIDENCE_FINAL}, reports)
        rel = read_release(info)
        self.assertEqual(rel["outcome"], capture_mod.EVIDENCE_FINAL)
        offsets = {int(r.get("release_r") or rel["record"]["offset_r"]) for r in reports} | {int(rel["record"]["offset_r"])}
        self.assertEqual(len(offsets), 1, (offsets, reports))
        self.assertEqual(len([p for p in os.listdir(owner_dir(info)) if ".release." in p and ".tmp." in p]), 0)


# =====================================================================================
# Watcher-death cuts: C1, C2, C3, C7, C9 -- the supervisor / a successor names the outcome
# =====================================================================================
class WatcherDeathCuts(_CutCase):
    def _spawn_here(self, run_id: str, agent: str = SUCCESS + "exit 0\n", *, pump: bool = True):
        session, sentinel = spawn_session(self.room, agent, run_id=run_id,
                                          binding_mode="session_field", binding_field="session_id",
                                          pump_until_sentinel=pump)
        self._pids += [int(session.pty["leader_pid"]), int(session.pty["pid"])]
        return session, Path(str(sentinel))

    def test_c1_watcher_killed_before_the_marker_is_boundary_unproven(self) -> None:
        """C1: the watcher is SIGKILLed while the root still runs; the root exits later with
        nobody to reap it or write a marker.  A successor with a proven exit (the pinned pid
        is positively absent) is `boundary_unproven`; without one `exit_unproven`.  Never a
        fence."""
        session, _sentinel = self._spawn_here("c1", "sleep 30\n", pump=False)
        leader, agent = int(session.pty["leader_pid"]), int(session.pty["pid"])
        os.kill(leader, signal.SIGKILL)
        wait_dead(leader)
        with contextlib.suppress(ProcessLookupError):   # Linux: the kernel SIGHUP already ended it
            os.kill(agent, signal.SIGKILL)
        wait_for(lambda: pty_supervisor._pid_presence(agent) == "absent" or pty_supervisor.proc_start_ticks(agent) == 0,
                 seconds=10, what="the root's death")
        session.pty["master_fd"] = -1                  # the supervisor's own view: masterless now
        drained = session.drain_after_exit(budget_ms=1000)
        self.assertEqual(drained.get("outcome"), capture_mod.OUTCOME_BOUNDARY_UNPROVEN, drained)
        session.exit_proof = {"proven": True, "how": "ladder"}
        drained = session.drain_after_exit(budget_ms=1000)
        self.assertEqual(drained.get("outcome"), capture_mod.OUTCOME_BOUNDARY_UNPROVEN, drained)
        self.assertFalse(session._fence_path() and os.path.exists(session._fence_path()))
        self.assertFalse(rt._stream_is_final(drained))
        self.assertEqual(session.await_completion()["state"], "LOST")

    def test_c2_watcher_killed_after_marker_before_sentinel_never_completes(self) -> None:
        """C2: the watcher is SIGKILLed paused inside `write_exit_sentinel` (marker already in
        the stream).  The supervisor proves the root's exit per pid (its pinned pid is absent
        -- it was reaped by the watcher) and publishes `exit.how=ladder, code=None`; the
        settlement is LOST (`cause_unreported`), never COMPLETED."""
        pause = Pause(self.room.path)
        pause.wrap(pty_supervisor, "write_exit_sentinel", "c2", in_watcher=True)
        self.addCleanup(setattr, pty_supervisor, "write_exit_sentinel", pty_supervisor.write_exit_sentinel.__wrapped__
                        if hasattr(pty_supervisor.write_exit_sentinel, "__wrapped__") else _REAL_WRITE_SENTINEL)
        session, sentinel = self._spawn_here("c2", pump=False)
        wait_for(pause.file("c2").exists, seconds=20, what="cut c2")
        leader, agent = int(session.pty["leader_pid"]), int(session.pty["pid"])
        os.kill(leader, signal.SIGKILL)
        wait_dead(leader)
        self.assertFalse(sentinel.exists())
        for _ in range(50):
            if capture_mod.find_marker(session.capture.raw(), session.fence_nonce)[0] >= 0:
                break
            session.pump(timeout_ms=50)
        self.assertGreaterEqual(capture_mod.find_marker(session.capture.raw(), session.fence_nonce)[0], 0)
        self.assertEqual(pty_supervisor.read_identity(agent)["start_state"], "absent")
        session.exit_proof = {"proven": True, "how": "ladder"}
        drained = session.drain_after_exit(budget_ms=3000)
        self.assertTrue(rt._stream_is_final(drained), drained)
        fence = capture_mod.read_capture_fence(session._fence_path(), fence=session.fence)
        self.assertEqual((fence["record"]["exit"]["how"], fence["record"]["exit"]["code"]), ("ladder", None))
        result = session.await_completion()
        self.assertNotEqual(result["state"], "COMPLETED", result)
        self.assertEqual(result["state"], "LOST", result)

    def test_c3_watcher_killed_after_sentinel_the_supervisor_names_the_tail(self) -> None:
        """C3: the watcher is SIGKILLed paused inside `_defer_for_release` (after the sentinel).
        S drains to the marker and publishes the SAME proof; the release cannot be served ->
        `diagnostic_tail_unaccounted` + `release_record_missing`, settlement COMPLETED."""
        pause = Pause(self.room.path)
        pause.wrap(pty_supervisor, "_defer_for_release", "c3", in_watcher=True)
        self.addCleanup(setattr, pty_supervisor, "_defer_for_release", _REAL_DEFER)
        session, _sentinel = self._spawn_here("c3")
        wait_for(pause.file("c3").exists, seconds=20, what="cut c3")
        leader = int(session.pty["leader_pid"])
        os.kill(leader, signal.SIGKILL)
        wait_dead(leader)
        drained = session.drain_after_exit(budget_ms=3000)
        self.assertTrue(rt._stream_is_final(drained), drained)
        released = session._release_two_phase()
        self.assertEqual(released["outcome"], capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED, released)
        self.assertEqual(released.get("release_record"), capture_mod.OUTCOME_RELEASE_RECORD_MISSING)
        self.assertEqual(settle(session)["state"], "COMPLETED")

    def test_c7_watcher_killed_during_orphan_finalize_the_successor_supersedes_it(self) -> None:
        """C7: S is SIGKILLed mid-drain (C4), then the ORPHAN watcher is SIGKILLed paused after
        claiming g1, before its fence.  A successor finds g1's owner positively dead (per-pid
        identity read), pins it, links g2 and publishes from the captured marker; 4
        concurrent successors link exactly one g2."""
        def install(pause: Pause, sup: int) -> None:
            pause.wrap(capture_mod, "write_capture_fence", "c7w", in_watcher=True, supervisor_pid=sup)
            real = os.read
            state = {"n": 0}

            def gated_read(fd, size):
                if os.getpid() == sup:
                    state["n"] += 1
                    if state["n"] == 1:
                        pause.mark_and_block("c7s")
                return real(fd, size)
            rt.StandaloneSession._cut_reader = staticmethod(gated_read)

        def steps(session) -> None:
            sentinel = pty_supervisor.exit_sentinel_path(self.room.path / "art", session.run_id,
                                                         session.session_id, session.incarnation)
            wait_for(Path(str(sentinel)).exists, seconds=20, what="the sentinel")
            session._master_reader = rt.StandaloneSession._cut_reader
            session.drain_after_exit(budget_ms=3000)
        info = supervisor_child(self.room, run_id="c7", cut="c7", install=install, steps=steps)
        self._track(info)
        wait_for(Pause(self.room.path).file("c7s").exists, seconds=20, what="cut c7 (supervisor)")
        kill_child(info)
        wait_for(Pause(self.room.path).file("c7w").exists, seconds=20, what="cut c7 (watcher)")
        highest, g1, _ = capture_mod.read_generations(owner_dir(info), info["incarnation"])
        self.assertEqual(highest, 1)
        self.assertEqual(int(g1["owner"]["pid"]), int(info["leader_pid"]))
        os.kill(int(info["leader_pid"]), signal.SIGKILL)
        wait_dead(int(info["leader_pid"]))
        self.assertFalse(fence_path(info).exists())
        reports = racing_successors(self.room, info, 4)
        finals = [r for r in reports if r.get("finality") == rt.FINALITY_CAPTURE_FINALIZED]
        self.assertEqual(len(finals), 4, reports)
        self.assertEqual({r.get("owner") for r in finals}, {2}, reports)
        highest, g2, _ = capture_mod.read_generations(owner_dir(info), info["incarnation"])
        self.assertEqual(highest, 2)
        self.assertEqual(int(g2["superseded"]["pid"]), int(info["leader_pid"]))
        s = successor(self.room, info)
        _same_proof(self, info, s.drain_after_exit(budget_ms=3000))
        self.assertEqual(settle(s)["state"], "COMPLETED")

    def test_c9_watcher_killed_between_release_1_and_2_keeps_the_record(self) -> None:
        """C9: S served release-1 and observed R; the watcher is SIGKILLed before release-2.
        The record stays `final`; the fence is untouched; settlement COMPLETED."""
        session, _sentinel = self._spawn_here("c9")
        drained = session.drain_after_exit(budget_ms=3000)
        self.assertEqual(drained["ended"], "marker", drained)
        leader = int(session.pty["leader_pid"])
        real = pty_supervisor._signal_drain_handoff

        def _kill_then_release(s):
            os.kill(leader, signal.SIGKILL)
            wait_dead(leader)
            real(s)
        pty_supervisor._signal_drain_handoff = _kill_then_release
        self.addCleanup(setattr, pty_supervisor, "_signal_drain_handoff", real)
        released = session._release_two_phase()
        self.assertEqual(released["state"], capture_mod.EVIDENCE_FINAL, released)
        self.assertEqual(capture_mod.read_release_record(session._release_path(), fence=session.fence)["outcome"],
                         capture_mod.EVIDENCE_FINAL)
        self.assertEqual(settle(session)["state"], "COMPLETED")


_REAL_WRITE_SENTINEL = pty_supervisor.write_exit_sentinel
_REAL_DEFER = pty_supervisor._defer_for_release


if __name__ == "__main__":
    unittest.main()
