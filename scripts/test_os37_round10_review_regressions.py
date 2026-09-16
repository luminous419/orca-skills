"""OS-37 BUGFIX round 10 (run_5855732a7f74): the consolidated follow-up review of `bcd5c6d`
(issuecomment-5688372349) -- FOUR merge blockers plus three same-boundary follow-ups.  Every
lock here is RED at `bcd5c6d` and GREEN at the corrected head, and exercises the PRODUCTION
wiring rather than a reimplementation.

1. [P1] macOS: the exit watcher's exit can discard the unread PTY tail and the supervisor
   still settles success.  On darwin a session leader's exit REVOKES the controlling tty and
   discards the master's unread tail, so a watcher that reaped the agent, wrote the sentinel
   and exited AT ONCE manufactured the very hangup the supervisor read as capture finality --
   over a capture the revoke had just truncated (a success record followed by a final failure
   record, the failure record lost, `COMPLETED/succeeded`).  The fix keeps the supervisor the
   SINGLE finalizing owner: the supervisor-alive watcher DEFERS its exit (a drain-handoff
   pipe) until the supervisor has drained the whole tail while the tty is alive; the
   supervisor then RELEASES the watcher, whose revoke delivers a REAL post-drain hangup that
   ends the drain -- so the drain still ends only on the hangup (round-8/9 contract), yet the
   tail is drained BEFORE the revoke rather than discarded by it.

2. [P1] unreadable slave-holder authority != proven absence.  `_slave_holders` /
   `_slave_holder_state` is now tri-state (`present` / `proven_absent` / `unreadable`); only a
   COMPLETE positive absence proof yields `proven`, and a failed `tcgetpgrp`, an unreadable
   `/proc` or a skipped `/proc/<pid>/fd` is `unreadable` with the failed authority NAMED.

3. [P2] a torn authority-upgrade log tail permanently blocked recovery.
   `read_authority_upgrade_records` now consumes only newline-terminated records and
   quarantines ONLY the final unterminated fragment; a complete corrupt record still RAISES.

4. [P2] explicit prompt migration pre-published the composition before its `prepared` record.
   `migrate_standalone_prompt_composition` no longer persists the composition before the
   two-phase writer; the two-phase function is the only publication path.

Follow-ups: (a) `_watch` re-checks the supervisor guard after reaping so simultaneous readiness
never skips orphan finalization; (b) `recover_handle`'s exit-evidence wait is derived from
`post_exit_drain_budget_ms`; (c) `tcgetpgrp() <= 0` is "no usable foreground group", never a
`killpg(0, 0)`.
"""
from __future__ import annotations

import contextlib
import ctypes
import errno
import json
import os
import shutil
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path

from scripts.deterministic_workflow import (launcher,  # noqa: E402
                                            standalone_capture as capture_mod,
                                            standalone_pty as pty_supervisor)
from scripts.deterministic_workflow.standalone_profile import profile_from_mapping  # noqa: E402
from scripts.deterministic_workflow.standalone_runtime import _tty_name as _tty  # noqa: E402
from scripts.test_os37_followup_review_regressions import (  # noqa: E402
    LANGGRAPH_REASON, _langgraph_ok)
from scripts.test_os37_round8_review_regressions import (  # noqa: E402
    _replay_legacy_run_without_composition)

REPO = Path(__file__).resolve().parent.parent


def _sh_profile(worktree: str, *, drain_ms: int = 2_000) -> object:
    return profile_from_mapping({
        "driver": "claude", "binary": "sh", "supported_range": [[1, 0, 0], [9, 0, 0]],
        "bin_dirs": ["/bin"], "worktree": worktree,
        "readiness_records": [{"channel": "structured", "record_type": "system",
                               "session_field": "session_id"}],
        "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
        "identity_flag": "--session-id",
        "timeouts": {"post_exit_drain_budget_ms": drain_ms}})


# =====================================================================================
# Item 1 -- macOS lost-tail: the supervisor-alive watcher DEFERS its exit so its
# session-leader revoke cannot manufacture / truncate the hangup the supervisor drains on
# =====================================================================================
class Item1MacOSLostTailTests(unittest.TestCase):
    """[P1] On darwin a session leader's exit REVOKES the controlling tty and DISCARDS the
    master's unread tail, so a watcher that reaped the agent, wrote the sentinel and exited AT
    ONCE manufactured -- and truncated -- the hangup the supervisor read as capture finality.
    The fix DEFERS the supervisor-alive watcher's exit (a drain-handoff pipe) until the
    supervisor has drained; the supervisor drains the whole tail while the tty is alive and
    only THEN releases the watcher, whose revoke delivers a REAL post-drain hangup.

    At `bcd5c6d` the watcher exits the instant it writes the sentinel -- there is no
    drain-handoff at all -- and the supervisor's drain that runs after the revoke can read a
    truncated (or empty) tail.  The deterministic discriminator here is the presence of that
    deferred-exit machinery (platform-independent -- RED at `bcd5c6d` everywhere); the
    tail-preservation it buys is confirmed behaviourally (a real pty on any platform: on
    Linux the slave's own close gives the hangup, on darwin the released watcher's revoke
    does) and the raw darwin revoke-truncation is reproduced at `bcd5c6d` in
    `evidence/repro/`.  No platform gate, so no CI-lane tolerated-skip is needed."""

    def setUp(self) -> None:
        self.room = Path(tempfile.mkdtemp(prefix="os37-r10-tail-")).resolve()
        self.addCleanup(shutil.rmtree, self.room, True)
        self._sessions: list[dict] = []

    def tearDown(self) -> None:
        for s in self._sessions:
            with contextlib.suppress(Exception):
                pty_supervisor.reap_leader(s, timeout_ms=2000)
                pty_supervisor.release(s)
            for pid in (s.get("leader_pid"), s.get("pid")):
                with contextlib.suppress(OSError, TypeError):
                    os.kill(int(pid), signal.SIGKILL)

    def _spawn(self, agent_body: str, *, session_id="sess", incarnation="inc1",
               capture: str | None = None):
        agent = self.room / f"agent-{session_id}-{incarnation}.sh"
        agent.write_text("#!/bin/sh\n" + agent_body)
        agent.chmod(0o755)
        base = self.room / "art"
        sentinel = pty_supervisor.exit_sentinel_path(base, "run_t", session_id, incarnation)
        os.makedirs(os.path.dirname(sentinel), exist_ok=True)
        capture = capture or str(Path(os.path.dirname(sentinel)) / "capture.log")
        session = pty_supervisor.spawn(
            argv=["/bin/sh", str(agent)], env={"PATH": "/bin:/usr/bin"},
            profile=_sh_profile(str(self.room)), session_id=session_id,
            incarnation=incarnation,
            spawn_record_target=str(pty_supervisor.spawn_record_path(
                base, "run_t", "i", incarnation)),
            cwd=str(self.room), sentinel=str(sentinel), fence=f"{session_id}:{incarnation}",
            image="/bin/sh", capture=capture)
        self._sessions.append(session)
        return session, Path(str(sentinel)), Path(capture)

    def test_the_spawn_and_watch_provide_the_deferred_exit_machinery(self) -> None:
        """The DETERMINISTIC discriminator: `spawn` hands the supervisor a drain-handoff
        write end and the pty module has the deferred-exit / release primitives.  At
        `bcd5c6d` there is no `drain_handoff_fd` and no `_await_drain_handoff` /
        `_signal_drain_handoff` at all -- the watcher exits the instant it writes the
        sentinel, so on darwin its session-leader revoke manufactures (and can truncate) the
        hangup the supervisor drains on.  The tail-preservation behaviour this enables is
        confirmed green below and reproduced at `bcd5c6d` in `evidence/repro/`."""
        session, sentinel, _cap = self._spawn(
            "printf '{\"is_error\":false}\\n'\nexit 0\n")
        self.assertIn("drain_handoff_fd", session,
                      "spawn does not provide the drain handoff; the watcher cannot defer "
                      "its exit and its revoke manufactures the hangup")
        self.assertGreaterEqual(int(session["drain_handoff_fd"]), 0)
        self.assertTrue(hasattr(pty_supervisor, "_await_drain_handoff"),
                        "no deferred-exit primitive")
        self.assertTrue(hasattr(pty_supervisor, "_signal_drain_handoff"),
                        "no drain-handoff release primitive")
        deadline = time.time() + 15
        while not sentinel.exists() and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(sentinel.exists(), "the watcher never wrote the exit sentinel")

    def test_the_supervisor_drain_preserves_the_final_tail_before_the_revoke(self) -> None:
        """A real `StandaloneSession.drain_after_exit` over a production-spawned pty: the
        agent writes a success record then a FINAL FAILURE record and exits; the supervisor's
        drain runs after a delay (a slow supervisor -- at `bcd5c6d` the watcher's revoke has
        already discarded the tail by then).  Here the deferring watcher keeps the tty alive,
        the drain reads the WHOLE tail, then releases the watcher for a real hangup."""
        from scripts.deterministic_workflow import standalone_journal as journal_mod
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        session = StandaloneSession(
            intent={"intent_id": "i-tail", "run_id": "run_t", "role": "WORKER"},
            profile=_sh_profile(str(self.room)), artifact_base=self.room / "art",
            run_id="run_t", journal=journal_mod.ExecutionJournal(self.room / "art",
                                                                 "run_t"))
        cap = str(session.capture.path)
        os.makedirs(os.path.dirname(cap), exist_ok=True)
        spawn, sentinel, _cap = self._spawn(
            "printf '{\"type\":\"result\",\"is_error\":false}\\n'\n"
            "printf '{\"type\":\"result\",\"is_error\":true}\\n'\n"
            "exit 0\n",
            session_id=session.session_id, incarnation=session.incarnation, capture=cap)
        session.pty = spawn
        session.record = {"pid": spawn["pid"], "captured_tty": _tty(spawn["slave_name"])}
        deadline = time.time() + 15
        while not sentinel.exists() and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(sentinel.exists())
        time.sleep(0.4)          # a slow supervisor: at bcd5c6d the revoke has fired by now
        drained = session.drain_after_exit(budget_ms=4000)
        self.assertEqual(drained["ended"], "hangup", drained)
        self.assertEqual(drained["finality"], "capture_finalized", drained)
        transcript = session.capture.transcript()
        self.assertIn('"is_error":true', transcript,
                      "the FINAL failure record was revoke-discarded before the drain")
        self.assertIn('"is_error":false', transcript)


# =====================================================================================
# Iteration 2 correction -- watcher exit / revoke must NEVER manufacture the proof
# =====================================================================================
from scripts.deterministic_workflow.standalone_runtime import (  # noqa: E402
    StandaloneSession, _stream_is_final)
from scripts.deterministic_workflow import standalone_journal as _journal_mod  # noqa: E402


class Iteration2FinalityBoundaryTests(unittest.TestCase):
    """[P1] The controlling invariant the iteration-1 reviewer required: watcher exit /
    revoke must NEVER itself establish the proof the supervisor consumes.  The supervisor
    releases the deferring watcher (and accepts its revoke EOF as the end) ONLY after it has
    obtained a COMPLETE positive slave-absence proof WHILE THE WATCHER IS STILL HELD; a
    `present` or `unreadable` holder authority never releases and ends the drain `unproven`
    (typed `stream_end_unproven`), and a darwin EOF this supervisor did not authorise is
    `unproven` too.

    Iteration-1 tree (`evidence/iter1/`): `drain_after_exit` released the watcher after 150
    ms of QUIET alone, with no holder proof, so a retained-slave descendant that wrote a late
    record -- or a watcher that exited on its own -- manufactured the proven hangup and the
    settlement COMPLETED.  Every lock below is RED there and GREEN here."""

    def setUp(self) -> None:
        self.room = Path(tempfile.mkdtemp(prefix="os37-r10i2-")).resolve()
        self.addCleanup(shutil.rmtree, self.room, True)
        self._sessions: list[dict] = []

    def tearDown(self) -> None:
        for s in self._sessions:
            with contextlib.suppress(Exception):
                pty_supervisor.reap_leader(s, timeout_ms=2000)
                pty_supervisor.release(s)
            for pid in (s.get("leader_pid"), s.get("pid")):
                with contextlib.suppress(OSError, TypeError):
                    os.kill(int(pid), signal.SIGKILL)
            # a retained-slave descendant is in the agent's process group; sweep it too.
            with contextlib.suppress(OSError, TypeError):
                os.killpg(int(s.get("pgid") or 0), signal.SIGKILL)

    def _wired(self, agent_body: str, *, run_id: str, budget_ms: int = 1500):
        """A real `StandaloneSession` wired to a production-spawned pty running ``agent_body``
        (an ``sh`` script).  Returns ``(session, sentinel_path)`` after the watcher has
        reaped the agent and written the sentinel (so `drain_after_exit`'s exit-proven
        precondition holds), the watcher DEFERRING its exit."""
        session = StandaloneSession(
            intent={"intent_id": f"i-{run_id}", "run_id": run_id, "role": "WORKER"},
            profile=_sh_profile(str(self.room), drain_ms=budget_ms),
            artifact_base=self.room / "art", run_id=run_id,
            journal=_journal_mod.ExecutionJournal(self.room / "art", run_id))
        cap = str(session.capture.path)
        os.makedirs(os.path.dirname(cap), exist_ok=True)
        agent = self.room / f"agent-{run_id}.sh"
        agent.write_text("#!/bin/sh\n" + agent_body)
        agent.chmod(0o755)
        base = self.room / "art"
        sentinel = pty_supervisor.exit_sentinel_path(base, run_id, session.session_id,
                                                     session.incarnation)
        os.makedirs(os.path.dirname(sentinel), exist_ok=True)
        spawn = pty_supervisor.spawn(
            argv=["/bin/sh", str(agent)], env={"PATH": "/bin:/usr/bin"},
            profile=_sh_profile(str(self.room), drain_ms=budget_ms),
            session_id=session.session_id, incarnation=session.incarnation,
            spawn_record_target=str(pty_supervisor.spawn_record_path(
                base, run_id, "i", session.incarnation)),
            cwd=str(self.room), sentinel=str(sentinel), fence=session.fence,
            image="/bin/sh", capture=cap)
        self._sessions.append(spawn)
        session.pty = spawn
        session.record = {"pid": spawn["pid"], "pgid": spawn["pid"],
                          "captured_tty": _tty(spawn["slave_name"])}
        # Iteration 4: capture the stream CONTINUOUSLY while waiting for the exit sentinel,
        # exactly as the production supervisor's `await_completion` pumps.  The pty now has no
        # controlling terminal (the kernel hangup is the finality proof), so the master's
        # unread buffer must be drained promptly -- a supervisor that read nothing for a while
        # after the agent's last slave close would let the kernel reclaim the tail.  A reader
        # that keeps up (production, and this helper) captures the whole tail; the subsequent
        # `drain_after_exit` then observes the hangup over an already-complete capture.
        deadline = time.time() + 15
        while not Path(str(sentinel)).exists() and time.time() < deadline:
            session.pump(timeout_ms=20)
        self.assertTrue(Path(str(sentinel)).exists(), "the watcher never wrote the sentinel")
        session.pump(timeout_ms=50)
        return session, Path(str(sentinel))

    def _proof(self, session) -> dict:
        return capture_mod.read_capture_finalized(session._finalized_path(),
                                                  fence=session.fence)

    # -- Lock 1: retained-slave descendant + late final failure record -------------------
    def test_a_retained_slave_descendant_with_a_late_final_record_is_unproven(self) -> None:
        """The agent emits a success record and EXITS; a descendant it forked KEEPS the
        slave, stays quiet past the settle window, then writes a final FAILURE record and
        holds the slave past the drain budget.  The supervisor must never turn that into a
        proven hangup: the drain ends `unproven` (a holder was present at every quiet check),
        names the holder, and the finality gate would settle `stream_end_unproven` -- NOT
        COMPLETED.  RED at `bcd5c6d`/iter1 (released on quiet before the late record, revoke
        discarded it, COMPLETED)."""
        session, _sent = self._wired(
            "printf '{\"type\":\"result\",\"is_error\":false}\\n'\n"
            "( sleep 0.4; printf '{\"type\":\"result\",\"is_error\":true}\\n'; sleep 5 ) &\n"
            "exit 0\n", run_id="run_r10i2_retain", budget_ms=1500)
        drained = session.drain_after_exit(budget_ms=1500)
        self.assertNotEqual(drained.get("finality"), "capture_finalized",
                            f"a retained-slave descendant manufactured a proven hangup: {drained}")
        self.assertFalse(_stream_is_final(drained))
        self.assertEqual(drained.get("ended"), "budget", drained)
        holders = drained.get("holders") or {}
        self.assertTrue(holders.get("rows"),
                        f"the retained-slave holder was not named in the evidence: {holders}")
        proof = self._proof(session)
        self.assertEqual(proof["outcome"], capture_mod.FINALITY_UNPROVEN, proof)

    # -- Lock 5: positive path, no descendant, complete absence proof --------------------
    def test_the_positive_path_with_no_descendant_is_proven_with_both_records(self) -> None:
        """Lock 5 (positive path) AND Lock 4 (other-owner mutation is caught).  No descendant:
        complete absence proof -> release -> hangup -> PROVEN, both records captured.  And the
        SUPERVISOR is the single finalizing owner: even after a SECOND component (the exit
        watcher, or any other) forges a proof at the fence, the supervisor's own finalization
        is the one that stands -- the other-owner record never becomes the consumed proof."""
        session, _sent = self._wired(
            "printf '{\"type\":\"result\",\"is_error\":false}\\n'\n"
            "printf '{\"type\":\"result\",\"is_error\":true}\\n'\n"
            "exit 0\n", run_id="run_r10i2_pos", budget_ms=4000)
        # Lock 4: a SECOND owner (the exit watcher) writes a proof BEFORE the supervisor
        # finalizes -- a forged PROVEN record with a bogus digest.  The supervisor's single
        # finalization must override it, so the watcher's proof never stands as the consumed
        # one (in production the supervisor-alive watcher writes NO proof at all; this forces
        # the adversarial case and confirms the single owner wins).
        capture_mod.write_capture_finalized(
            session._finalized_path(), fence=session.fence,
            finality=capture_mod.FINALITY_PROVEN, writer=capture_mod.WRITER_EXIT_WATCHER,
            ended="hangup", errno_name="", total_bytes=999999, sha256="deadbeef" * 8,
            records=999, exit_how="exit_sentinel", exit_code=0, holders={}, detail="forged")
        drained = session.drain_after_exit(budget_ms=4000)
        self.assertEqual(drained.get("ended"), "hangup", drained)
        self.assertTrue(_stream_is_final(drained), drained)
        transcript = session.capture.transcript()
        self.assertIn('"is_error":false', transcript)
        self.assertIn('"is_error":true', transcript)
        proof = self._proof(session)
        self.assertEqual(proof["outcome"], capture_mod.FINALITY_PROVEN, proof)
        # Single finalizing owner: the SUPERVISOR wrote the proof; the forged watcher proof
        # (bogus digest / record count) did NOT stand.
        self.assertEqual(proof["record"]["writer"], capture_mod.WRITER_SUPERVISOR)
        self.assertNotEqual(proof["record"]["sha256"], "deadbeef" * 8, proof)
        self.assertEqual(int(proof["record"]["total_bytes"]), session.capture.size, proof)

    # -- Lock 6: a watcher that exits before the supervisor authorises it is unproven -----
    def test_a_watcher_exit_before_an_authorised_release_is_unproven(self) -> None:
        """The review's original repro on the fixed tree: the exit watcher exits (revokes the
        tty) BEFORE the supervisor's drain authorises its release.  The hangup then arrives
        with the watcher already GONE, so any holder probe is POST-revoke and cannot vouch for
        a discarded tail -- the supervisor must refuse it as `watcher_exit_unproven`; RED at
        iter1 (the EOF took the unconditional proven branch -> COMPLETED).  The discriminator
        is the watcher's LIVENESS at the hangup (`_leader_alive`): a hangup with the watcher
        gone (here) is unproven, while a hangup with the watcher still HELD is the agent's own
        end-of-stream and proves once absence is shown (locked by
        `test_an_agent_hangup_while_the_watcher_is_held_is_proven`).  This test RUNS on every
        platform (no skip -- the CI-lane manifest only declares platform gates in
        `test_review_isolation`), but the darwin arm is the load-bearing lock: on Linux the
        agent's own last slave close is the genuine hangup, independent of the watcher, so
        there the same scenario legitimately proves."""
        session, _sent = self._wired(
            "printf '{\"type\":\"result\",\"is_error\":false}\\n'\n"
            "printf '{\"type\":\"result\",\"is_error\":true}\\n'\n"
            "exit 0\n", run_id="run_r10i2_wexit", budget_ms=4000)
        leader = int(session.pty["leader_pid"])
        # Force the watcher to EXIT (revoking the tty) BEFORE the supervisor's drain
        # authorises it -- the exact race the review requires the fixed tree to refuse.  The
        # watcher exits on ANY drain-handoff readiness, so signalling the handoff here stands
        # in for a watcher that exited on its own / crashed / hit its ceiling; the darwin EOF
        # its revoke then delivers was NOT authorised by this drain's proven-absence path.
        pty_supervisor._signal_drain_handoff(session.pty)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                done, _ = os.waitpid(leader, os.WNOHANG)
            except ChildProcessError:
                break
            if done == leader:
                break
            time.sleep(0.02)
        time.sleep(0.2)                          # let the revoke settle
        drained = session.drain_after_exit(budget_ms=4000)
        if sys.platform == "darwin":
            # The load-bearing arm: the hangup arrives with the watcher already GONE and this
            # supervisor did not authorise the release -> unproven, never COMPLETED.
            self.assertNotEqual(drained.get("finality"), "capture_finalized",
                                f"a watcher-exit revoke was accepted as proof: {drained}")
            self.assertFalse(_stream_is_final(drained))
            self.assertEqual(drained.get("ended"), "watcher_exit_unproven", drained)
            proof = self._proof(session)
            self.assertEqual(proof["outcome"], capture_mod.FINALITY_UNPROVEN, proof)
        else:
            # On Linux the agent's own last slave close is a genuine hangup independent of
            # the watcher, so the same scenario legitimately proves; the watcher's exit is
            # not the fact that establishes it.
            self.assertIn(drained.get("ended"), ("hangup", "budget"), drained)

    # -- Lock 7: an agent hangup while the watcher is HELD proves (the darwin production
    #    reality this iteration corrects) ----------------------------------------------------
    def test_an_agent_hangup_while_the_watcher_is_held_is_proven(self) -> None:
        """The darwin fact the iteration-2 finality boundary must respect: the exit watcher
        holds NO slave descriptor (`standalone_pty.spawn`), so the LAST slave close -- and
        thus the master hangup -- is the AGENT's own, and it can arrive WHILE THE WATCHER IS
        STILL ALIVE and deferring (this is what the real codex/claude recovery dispatch does:
        `await_completion`'s `pump` has already drained the whole capture, so the supervisor's
        finalizing drain begins already at that agent hangup, `read == 0`).  That hangup is
        NOT a watcher revoke: the watcher is held, the tty is intact, no tail can have been
        discarded.  With no other holder on the tty the supervisor PROVES absence while the
        watcher is held and the hangup is genuine -> COMPLETED.

        RED on the pre-fix iteration-2 tree (a `bcd5c6d`+iter1 first-cut that treated EVERY
        unauthorised hangup as a revoke -> `watcher_exit_unproven` -> the claude/codex recovery
        E2E settled BLOCKED); GREEN here.  The complementary direction -- a hangup with the
        watcher already GONE -> `watcher_exit_unproven` -- is locked by
        `test_a_watcher_exit_before_an_authorised_release_is_unproven`; together they lock the
        `_leader_alive` discriminator.

        The production condition is a hangup readable on the master (`select` returns it, the
        read yields EOF) on the drain's FIRST iteration -- before any quiet window -- WHILE the
        watcher is still alive.  A plain `sh` agent cannot produce that over a real darwin pty
        (its master does not report the hangup while the session leader lives, so the drain
        would instead reach its quiet window and release), so the hangup is injected by pointing
        the drain's master fd at an already-hung-up pipe (write end closed) after the pump --
        an immediately-readable EOF -- while the REAL production watcher stays genuinely alive
        and deferring on the captured tty, which is what the drain's liveness probe reads.  The
        darwin arm is the load-bearing lock."""
        session, _sent = self._wired(
            "printf '{\"type\":\"result\",\"is_error\":false}\\n'\n"
            "printf '{\"type\":\"result\",\"is_error\":true}\\n'\n"
            "exit 0\n", run_id="run_r10i2_alive", budget_ms=4000)
        # Drain the real tail exactly as production's `await_completion` pump does, so the
        # finalizing drain begins already at the agent's hangup.
        for _ in range(50):
            if session.pump(timeout_ms=200) == 0:
                break
        transcript = session.capture.transcript()
        self.assertIn('"is_error":false', transcript, "the pump did not capture the records")
        self.assertIn('"is_error":true', transcript, "the pump did not capture the records")
        # The watcher is STILL ALIVE and deferring here (nothing released it): the session
        # leader is on the captured tty, which is what the drain's liveness probe reads.
        leader = int(session.pty["leader_pid"])
        snapshot = pty_supervisor.read_process_table(session.record["captured_tty"])
        self.assertIsNotNone(pty_supervisor.row_for(snapshot, leader),
                             "the deferring watcher must still hold the tty before the hangup")
        # Point the drain at an already-hung-up master: a pipe whose write end is closed reads
        # EOF on the FIRST `select`+read, exactly like the agent's own last slave close arriving
        # before any quiet window -- while the real watcher (above) is still alive on the tty.
        hup_r, hup_w = os.pipe()
        os.close(hup_w)

        def _close_hup() -> None:
            with contextlib.suppress(OSError):
                os.close(hup_r)
        self.addCleanup(_close_hup)
        session.pty["master_fd"] = hup_r
        drained = session.drain_after_exit(budget_ms=4000)
        if sys.platform == "darwin":
            self.assertEqual(drained.get("ended"), "hangup", drained)
            self.assertTrue(_stream_is_final(drained), drained)
            proof = self._proof(session)
            self.assertEqual(proof["outcome"], capture_mod.FINALITY_PROVEN, proof)
            self.assertEqual(proof["record"]["writer"], capture_mod.WRITER_SUPERVISOR)
            # the absence proof was taken WHILE THE WATCHER WAS HELD: no holder named.
            self.assertFalse((drained.get("holders") or {}).get("rows"), drained)
        else:
            # On Linux `settle_s == 0`: the slave close is the hangup outright, no guard.
            self.assertEqual(drained.get("ended"), "hangup", drained)
            self.assertTrue(_stream_is_final(drained), drained)

    # -- Lock 2: bytes readable at release time (a descendant timed to the release) -------
    def test_a_descendant_writing_at_release_time_is_never_lost_and_succeeded(self) -> None:
        """A descendant that keeps the slave and writes right around the settle window is a
        HOLDER at the quiet check, so the supervisor never releases the watcher into a
        hangup; the record it writes is therefore never lost-and-succeeded -- the drain is
        `unproven` (holder present) whether or not that byte was captured."""
        session, _sent = self._wired(
            "printf '{\"type\":\"result\",\"is_error\":false}\\n'\n"
            "( sleep 0.15; printf '{\"type\":\"result\",\"is_error\":true}\\n'; sleep 5 ) &\n"
            "exit 0\n", run_id="run_r10i2_race", budget_ms=1200)
        drained = session.drain_after_exit(budget_ms=1200)
        self.assertFalse(_stream_is_final(drained),
                         f"a descendant writing at the release window was lost-and-succeeded: "
                         f"{drained}")
        self.assertEqual(drained.get("ended"), "budget", drained)

    # -- Lock 8 (iteration 3, F1): a DETACHED (setsid) slave holder is a complete-absence
    #    counterexample -- `ps -t` is blind to it, the slave-descriptor scan is not ---------
    def test_a_detached_setsid_slave_holder_with_a_late_record_is_unproven(self) -> None:
        """The reviewer's iteration-2 counterexample (`detached_holder2.txt`): the agent
        emits a success record and forks a child that calls ``setsid()`` while KEEPING the
        pty slave as its 0/1/2, then exits; the child stays quiet past the settle window and
        writes a late FAILURE record.  Having left the tty session, the child is invisible to
        the tty-scoped ``ps -t`` probe, so at iteration 2 the empty tty table read as
        ``proven_absent`` and authorised the watcher release whose revoke truncated the
        child's tail (its late write got EIO) and the dispatch settled PROVEN over success
        only.  The COMPLETE slave-descriptor authority (``_slave_device_fd_holders`` via
        ``lsof`` on the slave DEVICE) catches the off-tty holder: absence is never proven, the
        watcher is never released, the drain ends by ``budget`` -> UNPROVEN with the holder
        NAMED, and -- no premature revoke -- the child's late record is preserved.  RED on the
        iteration-2 tree; darwin is the load-bearing arm."""
        agent = self.room / "detached_agent.py"
        pidfile = self.room / "holder.pid"
        agent.write_text(
            "import os, time, signal, sys\n"
            "sys.stdout.write('{\"type\":\"result\",\"is_error\":false}\\n'); sys.stdout.flush()\n"
            "if os.fork() == 0:\n"
            "    os.setsid()\n"                       # leave the tty session, KEEP slave 0/1/2
            "    signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
            "    open(%r, 'w').write(str(os.getpid()))\n" % str(pidfile) +
            "    time.sleep(0.4)\n"
            "    try: os.write(1, b'{\"type\":\"result\",\"is_error\":true}\\n')\n"
            "    except OSError: pass\n"
            "    time.sleep(5)\n"
            "    os._exit(0)\n"
            "os._exit(0)\n")
        session, _sent = self._wired(f"exec {sys.executable} {agent}\n",
                                     run_id="run_r10i3_detached", budget_ms=1500)
        deadline = time.time() + 5
        while not pidfile.exists() and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(pidfile.exists(), "the detached holder never recorded its pid")
        holder = int(pidfile.read_text())

        def _kill_holder() -> None:
            with contextlib.suppress(OSError):
                os.kill(holder, signal.SIGKILL)
        self.addCleanup(_kill_holder)
        os.kill(holder, 0)                            # the off-tty holder is alive
        drained = session.drain_after_exit(budget_ms=1500)
        holders = drained.get("holders") or {}
        if sys.platform == "darwin":
            self.assertEqual(drained.get("ended"), "budget", drained)
            self.assertFalse(_stream_is_final(drained), drained)
            self.assertIn(holder, holders.get("fd_holders") or [],
                          f"the detached slave holder was not named in the evidence: {holders}")
            proof = self._proof(session)
            self.assertEqual(proof["outcome"], capture_mod.FINALITY_UNPROVEN, proof)
            os.kill(holder, 0)                        # never revoked out from under it
            # the holder's late record was preserved (no premature revoke)
            self.assertIn('"is_error":true', session.capture.transcript(), drained)
        else:
            self.assertFalse(_stream_is_final(drained), drained)


# =====================================================================================
# Iteration 4 -- the COMPLETE, fail-closed libproc slave-descriptor authority (option B)
# =====================================================================================
class Iteration4LibprocAuthorityTests(Iteration2FinalityBoundaryTests):
    """[P1] Iteration 4/5: the darwin ``libproc`` slave-descriptor authority (option B) that gates
    the supervisor's release of the deferring watcher and the orphan finalizer.  Iteration 5
    corrects four native defects the iter-4 reviewer found INSIDE the authority (all verified
    against real darwin ``libproc`` semantics -- ``proc_listallpids`` returns an ENTRY COUNT; a
    process's identity read and its fd listing are permitted by the kernel iff it is our uid; the
    ``vnode_fdinfowithpath`` structure is 1200 bytes; a non-vnode or closed fd both read ``EBADF``):

    * F1 -- the pid listing uses the ENTRY count directly (never ``// sizeof(int32)``, which
      dropped three quarters of the table) and rejects truncation/growth.
    * F2 -- the gate for inspecting a process is FD INSPECTABILITY, not an identity read: a
      same-uid holder whose ``PROC_PIDTBSDINFO`` is denied is still caught because its fd LISTING
      succeeds; a process whose fd listing the kernel denies is (we are not root) provably not our
      uid and cannot hold a mode-0620 owner-uid slave (``other_uid``); changed-uid descendants are
      explicitly out of scope, not falsely claimed.
    * F3 -- a per-fd failure is a MOVING fd table (a holder can ``dup2`` the slave onto a former
      pipe fd and close the originals): the process is re-scanned over a FRESH listing until two
      consecutive complete identical scans agree, bounded; never a "raced, not a holder" skip.
    * F4 -- a fixed-offset decode requires the EXACT structure length; a positive SHORT read is
      ``unenumerable`` for that pid, never decoded from the zero-filled buffer.

    A per-fd ``EPERM`` is the ONE thing skipped (recorded, not silent): a pty slave vnode is never
    permission-restricted, so a fd macOS refuses to introspect is provably not the slave; without
    this a host with any TCC-restricted-fd launchd agent would be permanently ``unreadable``.  The
    real-PTY detached-holder / positive-path locks live in the parent (inherited)."""

    def _clean_slave(self):
        session, _sent = self._wired("printf 'x\\n'\nexit 0\n", run_id="run_r10i4_clean",
                                     budget_ms=1500)
        return session

    def test_the_deferring_watcher_holds_no_slave_descriptor(self) -> None:
        """The spawn invariant the whole model rests on: the exit watcher (session leader) holds
        NO slave descriptor, so the last slave close -- and thus the hangup its release delivers
        -- is the agent's own.  The libproc authority must NOT find the leader among the holders."""
        if sys.platform != "darwin":
            return
        session = self._clean_slave()
        leader = int(session.pty["leader_pid"])
        holders = pty_supervisor.slave_device_holders(session.pty["slave_name"])
        self.assertNotIn(leader, holders.get("holders") or [],
                         f"the exit watcher holds a slave descriptor: {holders}")

    def test_a_clean_slave_proves_absent_despite_other_uid_processes(self) -> None:
        """A clean slave (no same-uid holder) is `proven_absent` even though the host is full of
        OTHER-uid processes whose fd listings the kernel denies us -- they are the diagnostic
        `other_uid` bucket and never block the proof, and no genuinely-restricted same-uid fd
        makes the scan `unreadable`."""
        if sys.platform != "darwin":
            return
        session = self._clean_slave()
        session.drain_after_exit(budget_ms=1500)
        holders = pty_supervisor.slave_device_holders(
            session.pty["slave_name"],
            exclude_pids=(int(session.record["pid"]), int(session.pty["leader_pid"])))
        self.assertEqual(holders.get("state"), "proven_absent", holders)
        self.assertTrue(holders.get("other_uid"), "no other-uid processes were listed at all")
        self.assertFalse(holders.get("unenumerable"), holders)

    def test_the_pid_listing_returns_every_entry_not_a_quarter(self) -> None:
        """F1: `proc_listallpids` returns the number of PID ENTRIES, so the authority must scan
        the whole table.  The production listing must return ~as many pids as the raw native
        nonzero count -- NOT a quarter of it (the `// sizeof(int32)` regression scanned 1/4 and
        silently omitted a holder past the prefix, reviewer iter5 F1)."""
        if sys.platform != "darwin":
            return
        n = pty_supervisor._LIBPROC.proc_listallpids(None, 0)
        buf = (ctypes.c_int32 * (n + 256))()
        got = pty_supervisor._LIBPROC.proc_listallpids(buf, ctypes.sizeof(buf))
        native_nonzero = len([1 for i in range(len(buf)) if buf[i] > 0])
        pids, reason = pty_supervisor._libproc_list_all_pids()
        self.assertIsNotNone(pids, f"listing failed: {reason}")
        # the production listing is the whole table, not a quarter of it
        self.assertGreater(len(pids), (got // 4) + 8,
                           f"the listing looks byte-length-divided: {len(pids)} vs got//4={got // 4}")
        self.assertGreaterEqual(len(pids), int(native_nonzero * 0.6),
                                f"the listing dropped most of the table: {len(pids)} of {native_nonzero}")

    def test_a_quarter_count_listing_misses_a_tail_holder_MUTATION(self) -> None:
        """F1 mutation: a listing that keeps only the first quarter of the entries (the byte-length
        regression) drops a holder whose pid sits past that prefix, so the authority reads absence
        over a live holder.  That the real full-count listing prevents this is the lock."""
        if sys.platform != "darwin":
            return
        holder = self._spawn_setsid_holder()
        real = pty_supervisor._libproc_list_all_pids

        def _quarter(_real=real):
            pids, reason = _real()
            if pids is None:
                return pids, reason
            keep = pids[: max(1, len(pids) // 4)]
            if holder in pids and holder not in keep:
                return keep, None                 # holder pushed past the truncated prefix
            return [p for p in keep if p != holder], None
        pty_supervisor._libproc_list_all_pids = _quarter
        self.addCleanup(setattr, pty_supervisor, "_libproc_list_all_pids", real)
        holders = pty_supervisor.slave_device_holders(self._holder_slave)
        self.assertEqual(holders.get("state"), "proven_absent",
                         f"the quarter-count mutation did not hide the tail holder: {holders}")

    def test_a_holder_with_denied_identity_is_still_caught_by_fd_inspection(self) -> None:
        """F2: a live same-uid setsid holder whose IDENTITY read (`PROC_PIDTBSDINFO`) is denied is
        NEVER classified other-uid and skipped (the iter-4 defect).  Its fd LISTING still succeeds,
        so the authority inspects its descriptors directly and finds the slave -> `present`, never
        `proven_absent`.  The denied identity is not on the proof path at all."""
        if sys.platform != "darwin":
            return
        holder = self._spawn_setsid_holder()
        real = pty_supervisor._libproc_pidinfo

        def _deny_identity(pid, flavor, size, _real=real):
            if pid == holder and flavor == pty_supervisor._PROC_PIDTBSDINFO:
                return None, errno.EPERM
            return _real(pid, flavor, size)
        pty_supervisor._libproc_pidinfo = _deny_identity
        self.addCleanup(setattr, pty_supervisor, "_libproc_pidinfo", real)
        holders = pty_supervisor.slave_device_holders(self._holder_slave)
        self.assertEqual(holders.get("state"), "present", holders)
        self.assertIn(holder, holders.get("holders") or [],
                      f"the denied-identity holder was not caught via fd inspection: {holders}")

    def test_a_denied_fd_listing_is_other_uid_not_unenumerable(self) -> None:
        """F2: a process whose fd LISTING the kernel denies (`EPERM`) is, since we are not root,
        provably not our uid; a mode-0620 owner-uid pty slave cannot be open in it, so it is the
        diagnostic `other_uid` bucket and does NOT make the proof `unreadable`.  (Changed-uid
        `setuid` descendants are out of this authority's scope and are not claimed as covered.)"""
        if sys.platform != "darwin":
            return
        session = self._clean_slave()
        session.drain_after_exit(budget_ms=1500)
        me = os.getpid()
        real = pty_supervisor._libproc_list_vnode_fds

        def _deny_listing(pid, _real=real):
            if pid == me:
                return None, errno.EPERM
            return _real(pid)
        pty_supervisor._libproc_list_vnode_fds = _deny_listing
        self.addCleanup(setattr, pty_supervisor, "_libproc_list_vnode_fds", real)
        holders = pty_supervisor.slave_device_holders(
            session.pty["slave_name"],
            exclude_pids=(int(session.record["pid"]), int(session.pty["leader_pid"])))
        self.assertIn(me, holders.get("other_uid") or [], holders)
        self.assertFalse(any(u.get("pid") == me for u in holders.get("unenumerable") or ()), holders)
        self.assertEqual(holders.get("state"), "proven_absent", holders)

    def test_a_relocated_slave_is_caught_never_proven_absent(self) -> None:
        """F3: a holder that, DURING the scan, `dup2`s the slave onto a descriptor that was a pipe
        in the fresh listing and closes 0/1/2 (so the originals read EBADF) must NOT read as
        absent.  The stable double scan over a FRESH listing re-reads the fd types and finds the
        relocated slave -> `present` (or `unreadable` if the table never settles), never
        `proven_absent`."""
        if sys.platform != "darwin":
            return
        holder, cmd, ack = self._spawn_relocating_holder()
        real = pty_supervisor._libproc_fd_devino
        state = {"moved": False}

        def _seam(pid, fd, _real=real):
            if pid == holder and not state["moved"]:
                state["moved"] = True
                cmd.write_text("go")
                deadline = time.time() + 3
                while not ack.exists() and time.time() < deadline:
                    time.sleep(0.005)
            return _real(pid, fd)
        pty_supervisor._libproc_fd_devino = _seam
        self.addCleanup(setattr, pty_supervisor, "_libproc_fd_devino", real)
        holders = pty_supervisor.slave_device_holders(self._holder_slave)
        self.assertNotEqual(holders.get("state"), "proven_absent",
                            f"a relocated live slave was read as absent: {holders}")
        self.assertIn(holders.get("state"), ("present", "unreadable"), holders)

    def test_treating_a_relocation_ebadf_as_gone_is_caught_MUTATION(self) -> None:
        """F3 mutation: if a per-fd EBADF is treated as the whole process being gone, the holder's
        closed original descriptors drop the process before its FRESH listing can reveal the
        relocated slave -> absence over a live holder.  That the real code re-scans instead is the
        lock."""
        if sys.platform != "darwin":
            return
        holder, cmd, ack = self._spawn_relocating_holder()
        real = pty_supervisor._libproc_fd_devino
        state = {"moved": False}

        def _mutate(pid, fd, _real=real):
            if pid == holder and not state["moved"]:
                state["moved"] = True
                cmd.write_text("go")
                deadline = time.time() + 3
                while not ack.exists() and time.time() < deadline:
                    time.sleep(0.005)
            devino, e = _real(pid, fd)
            if devino is None and e == errno.EBADF:
                return None, errno.ESRCH          # MUTATION: EBADF read as process-gone
            return devino, e
        pty_supervisor._libproc_fd_devino = _mutate
        self.addCleanup(setattr, pty_supervisor, "_libproc_fd_devino", real)
        holders = pty_supervisor.slave_device_holders(self._holder_slave)
        self.assertEqual(holders.get("state"), "proven_absent",
                         f"the EBADF-as-gone mutation did not hide the relocated holder: {holders}")

    def test_a_bystander_that_closes_a_fd_once_still_proves_absent(self) -> None:
        """F3 positive: a BYSTANDER (not a holder) that closes a vnode fd once during the scan
        makes one per-fd query EBADF; the process is re-scanned over a fresh listing and, being
        clean and now stable, does NOT block the proof -- ordinary fd-table churn on a busy host
        still converges to `proven_absent` within the retry bound (the F01-under-load guarantee)."""
        if sys.platform != "darwin":
            return
        session = self._clean_slave()
        session.drain_after_exit(budget_ms=1500)
        me = os.getpid()
        real = pty_supervisor._libproc_fd_devino
        state = {"bumped": False}

        def _flap(pid, fd, _real=real):
            if pid == me and not state["bumped"]:
                state["bumped"] = True
                return None, errno.EBADF          # one transient closed-fd race, then stable
            return _real(pid, fd)
        pty_supervisor._libproc_fd_devino = _flap
        self.addCleanup(setattr, pty_supervisor, "_libproc_fd_devino", real)
        holders = pty_supervisor.slave_device_holders(
            session.pty["slave_name"],
            exclude_pids=(int(session.record["pid"]), int(session.pty["leader_pid"])))
        self.assertEqual(holders.get("state"), "proven_absent", holders)
        self.assertFalse(holders.get("unenumerable"), holders)

    def test_a_race_exited_candidate_is_gone_not_unreadable(self) -> None:
        """A same-uid candidate that EXITS during the scan (`proc_pidfdinfo` -> ESRCH) has
        released every descriptor at exit, so it is `gone` (diagnostic) and does NOT make the
        authority `unreadable`: a genuinely clean slave still proves absent while a busy host
        churns transient same-uid pids."""
        if sys.platform != "darwin":
            return
        session = self._clean_slave()
        session.drain_after_exit(budget_ms=1500)
        me = os.getpid()
        real = pty_supervisor._libproc_fd_devino

        def _vanish(pid, fd, _real=real):
            if pid == me:
                return None, errno.ESRCH
            return _real(pid, fd)
        pty_supervisor._libproc_fd_devino = _vanish
        self.addCleanup(setattr, pty_supervisor, "_libproc_fd_devino", real)
        holders = pty_supervisor.slave_device_holders(
            session.pty["slave_name"],
            exclude_pids=(int(session.record["pid"]), int(session.pty["leader_pid"])))
        self.assertEqual(holders.get("state"), "proven_absent", holders)
        self.assertIn(me, holders.get("gone") or [], holders)
        self.assertFalse(any(u.get("pid") == me for u in holders.get("unenumerable") or ()), holders)

    def test_a_short_per_fd_read_is_unenumerable(self) -> None:
        """F4: a positive but SHORT `proc_pidfdinfo` return (fewer than the 1200-byte structure)
        for a live holder's descriptors must never be decoded from the zero-filled buffer; the
        holder is `unenumerable` and the authority `unreadable`, never `proven_absent`."""
        if sys.platform != "darwin":
            return
        holder = self._spawn_setsid_holder()
        realL = pty_supervisor._LIBPROC

        class _Short:
            def __getattr__(self, name):
                return getattr(realL, name)

            def proc_pidfdinfo(self, pid, fd, flavor, buf, size):
                if pid == holder:
                    return 1                       # a positive SHORT read
                return realL.proc_pidfdinfo(pid, fd, flavor, buf, size)
        pty_supervisor._LIBPROC = _Short()
        self.addCleanup(setattr, pty_supervisor, "_LIBPROC", realL)
        holders = pty_supervisor.slave_device_holders(self._holder_slave)
        self.assertEqual(holders.get("state"), "unreadable", holders)
        self.assertTrue(any(u.get("pid") == holder for u in holders.get("unenumerable") or ()),
                        f"the short-read holder was not named unenumerable: {holders}")

    def test_accepting_a_short_read_is_caught_MUTATION(self) -> None:
        """F4 mutation: a decode that accepts a short read (the iter-4 `got <= 0`-only check)
        yields a garbage `(dev, ino)` that fails the match, so the live holder is silently missed
        and absence is wrongly proven.  That the real exact-size gate refuses the decode is the
        lock."""
        if sys.platform != "darwin":
            return
        holder = self._spawn_setsid_holder()
        real = pty_supervisor._libproc_fd_devino

        def _accept_short(pid, fd, _real=real):
            if pid == holder:
                return (0, 0), 0                   # MUTATION: a short/garbage read taken as real
            return _real(pid, fd)
        pty_supervisor._libproc_fd_devino = _accept_short
        self.addCleanup(setattr, pty_supervisor, "_libproc_fd_devino", real)
        holders = pty_supervisor.slave_device_holders(self._holder_slave)
        self.assertEqual(holders.get("state"), "proven_absent",
                         f"accepting a garbage short read failed to hide the holder: {holders}")

    def test_a_failed_process_listing_is_unreadable(self) -> None:
        """If `proc_listallpids` itself fails, the authority is `unreadable` -- the scan could not
        even begin, so absence is not proven."""
        if sys.platform != "darwin":
            return
        holder = self._spawn_setsid_holder()
        real = pty_supervisor._LIBPROC.proc_listallpids

        def _fail(*_a):
            ctypes.set_errno(errno.EPERM)
            return -1
        pty_supervisor._LIBPROC.proc_listallpids = _fail
        self.addCleanup(setattr, pty_supervisor._LIBPROC, "proc_listallpids", real)
        holders = pty_supervisor.slave_device_holders(self._holder_slave)
        self.assertEqual(holders.get("state"), "unreadable", holders)
        _ = holder

    def test_skipping_a_holder_pid_is_caught(self) -> None:
        """Adversarial mutation: make the authority SILENTLY return `proven_absent` despite a live
        holder (the lsof defect).  Under the mutation the inherited detached-holder lock's
        `unproven` assertion FAILS; MUTATION_CAUGHT confirms the lock depends on the authority."""
        if sys.platform != "darwin":
            return
        real = pty_supervisor.slave_device_holders

        def _skip_holder(slave_name, *, exclude_pids=()):
            return {"method": "libproc", "state": "proven_absent", "holders": [],
                    "unenumerable": [], "other_uid": [], "gone": [], "device": slave_name}
        pty_supervisor.slave_device_holders = _skip_holder
        try:
            caught = False
            try:
                self.test_a_detached_setsid_slave_holder_with_a_late_record_is_unproven()
            except AssertionError:
                caught = True
            self.assertTrue(caught, "skipping the holder was NOT caught: the lock does not "
                            "depend on the complete authority")
        finally:
            pty_supervisor.slave_device_holders = real

    # -- helper: a live same-uid setsid holder retaining the slave stdio ------------------
    def _spawn_setsid_holder(self) -> int:
        agent = self.room / "holder_only.py"
        pidfile = self.room / "holder2.pid"
        agent.write_text(
            "import os, time, signal, sys\n"
            "sys.stdout.write('x\\n'); sys.stdout.flush()\n"
            "if os.fork() == 0:\n"
            "    os.setsid()\n"
            "    signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
            "    open(%r,'w').write(str(os.getpid()))\n" % str(pidfile) +
            "    time.sleep(30)\n"
            "    os._exit(0)\n"
            "os._exit(0)\n")
        session, _sent = self._wired(f"exec {sys.executable} {agent}\n",
                                     run_id="run_r10i4_holder", budget_ms=1500)
        self._holder_slave = session.pty["slave_name"]
        deadline = time.time() + 5
        while not pidfile.exists() and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(pidfile.exists(), "the holder never recorded its pid")
        holder = int(pidfile.read_text())

        def _kill() -> None:
            with contextlib.suppress(OSError):
                os.kill(holder, signal.SIGKILL)
        self.addCleanup(_kill)
        return holder

    # -- helper: a live holder that RELOCATES the slave onto a former pipe fd on command ---
    def _spawn_relocating_holder(self):
        agent = self.room / "reloc.py"
        pidfile = self.room / "reloc.pid"
        cmd = self.room / "reloc.go"
        ack = self.room / "reloc.done"
        agent.write_text(
            "import os, time, signal, sys\n"
            "sys.stdout.write('x\\n'); sys.stdout.flush()\n"
            "if os.fork() == 0:\n"
            "    os.setsid()\n"
            "    signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
            "    r, w = os.pipe()\n"
            "    open(%r, 'w').write(str(os.getpid()))\n" % str(pidfile) +
            "    while not os.path.exists(%r):\n" % str(cmd) +
            "        time.sleep(0.005)\n"
            "    os.dup2(0, w)\n"
            "    for fd in (0, 1, 2):\n"
            "        try:\n"
            "            os.close(fd)\n"
            "        except OSError:\n"
            "            pass\n"
            "    open(%r, 'w').write(str(w))\n" % str(ack) +
            "    time.sleep(30)\n"
            "    os._exit(0)\n"
            "os._exit(0)\n")
        session, _sent = self._wired(f"exec {sys.executable} {agent}\n",
                                     run_id="run_r10i5_reloc", budget_ms=1500)
        self._holder_slave = session.pty["slave_name"]
        deadline = time.time() + 5
        while not pidfile.exists() and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(pidfile.exists(), "the relocating holder never recorded its pid")
        holder = int(pidfile.read_text())

        def _kill() -> None:
            with contextlib.suppress(OSError):
                os.kill(holder, signal.SIGKILL)
        self.addCleanup(_kill)
        return holder, cmd, ack





# =====================================================================================
# Item 2 -- the slave-holder probe is tri-state; unreadable is never absence  (+ follow-up c)
# =====================================================================================
class Item2SlaveHolderTriStateTests(unittest.TestCase):
    """[P1] `_slave_holders` records whether each probe COMPLETED, and
    `_slave_holder_state` yields `present` / `proven_absent` / `unreadable`.  At `bcd5c6d`
    `_slave_holders` collapsed a failed `tcgetpgrp` / unreadable `/proc` / skipped fd into
    `foreground_group_present=None, rows=[]`, and `_slave_holder_present` read that as
    ABSENCE -> `proven`."""

    def _real_master(self) -> int:
        import pty as _pty
        master, slave = _pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        self._slave_name = os.ttyname(slave)
        return master

    def test_tcgetpgrp_EIO_is_unreadable_and_names_the_authority(self) -> None:
        master = self._real_master()

        def _eio(_fd: int) -> int:
            raise OSError(errno.EIO, "injected")
        holders = pty_supervisor._slave_holders(master, self._slave_name, tcgetpgrp=_eio,
                                                isdir=lambda _p: False)
        self.assertEqual(pty_supervisor._slave_holder_state(holders), "unreadable")
        self.assertTrue(any(a.startswith("tcgetpgrp:") for a in holders["unreadable"]),
                        holders)

    def test_proc_listdir_EACCES_is_unreadable(self) -> None:
        master = self._real_master()

        def _eacces(_p: str) -> list:
            raise OSError(errno.EACCES, "injected")
        holders = pty_supervisor._slave_holders(
            master, self._slave_name,
            tcgetpgrp=lambda _fd: -1,               # no usable foreground group (follow-up c)
            isdir=lambda _p: True, listdir=_eacces)
        self.assertEqual(holders["proc_scan"], "incomplete")
        self.assertEqual(pty_supervisor._slave_holder_state(holders), "unreadable")
        self.assertTrue(any(a.startswith("proc_listdir:") for a in holders["unreadable"]))

    def test_a_skipped_proc_fd_entry_is_unreadable(self) -> None:
        master = self._real_master()

        def _listdir(path: str) -> list:
            if path == "/proc":
                return ["424242"]
            raise OSError(errno.EACCES, "fd dir unreadable")
        holders = pty_supervisor._slave_holders(
            master, self._slave_name, tcgetpgrp=lambda _fd: -1,
            isdir=lambda _p: True, listdir=_listdir)
        self.assertEqual(holders["proc_scan"], "incomplete")
        self.assertEqual(pty_supervisor._slave_holder_state(holders), "unreadable")
        self.assertTrue(any(a.startswith("proc_fd:424242") for a in holders["unreadable"]))

    def test_tcgetpgrp_non_positive_never_calls_killpg_and_is_absent(self) -> None:
        """Follow-up (c): `tcgetpgrp() <= 0` is "no usable foreground group", NOT a live
        holder, and `killpg(0, 0)` / `killpg(<neg>, 0)` (which would signal the watcher's own
        group) is NEVER called."""
        master = self._real_master()
        calls: list[int] = []

        def _killpg(pgid: int, _sig: int) -> None:
            calls.append(pgid)
        holders = pty_supervisor._slave_holders(
            master, self._slave_name, tcgetpgrp=lambda _fd: 0, killpg=_killpg,
            isdir=lambda _p: False)
        self.assertEqual(calls, [], "killpg was called for a non-positive foreground pgid")
        self.assertIs(holders["foreground_group_present"], False)
        self.assertEqual(pty_supervisor._slave_holder_state(holders), "proven_absent")

    def test_a_complete_empty_probe_is_proven_absent(self) -> None:
        master = self._real_master()
        holders = pty_supervisor._slave_holders(
            master, self._slave_name,
            tcgetpgrp=lambda _fd: 999999,
            killpg=lambda _p, _s: (_ for _ in ()).throw(ProcessLookupError()),
            isdir=lambda _p: False)
        self.assertEqual(pty_supervisor._slave_holder_state(holders), "proven_absent")

    def test_a_present_foreground_group_is_present(self) -> None:
        master = self._real_master()
        holders = pty_supervisor._slave_holders(
            master, self._slave_name, tcgetpgrp=lambda _fd: os.getpgrp(),
            killpg=lambda _p, _s: None, isdir=lambda _p: False)
        self.assertEqual(pty_supervisor._slave_holder_state(holders), "present")


# =====================================================================================
# Item 3 -- a torn authority-upgrade log tail no longer blocks recovery
# =====================================================================================
class Item3TornUpgradeTailTests(unittest.TestCase):
    """[P2] `read_authority_upgrade_records` skips ONLY the final unterminated fragment; a
    complete corrupt record still RAISES.  At `bcd5c6d` any torn tail raised
    `STANDALONE_MIGRATION_REFUSED` on every authority read, so recovery could never repair
    the run."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r10-torn-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.run_id = "run_torn"
        self.log = launcher._authority_upgrade_log_path(self.base, self.run_id)
        self.log.parent.mkdir(parents=True, exist_ok=True)

    def _committed_record(self, digest: str = "d1") -> dict:
        return {"schema": launcher.STANDALONE_AUTHORITY_UPGRADE_SCHEMA, "run_id": self.run_id,
                "upgrade_id": "u1", "attempt": 1, "state": launcher.MIGRATION_COMMITTED,
                "bound_prompt_composition_digest": digest, "actor": "op", "reason": "why"}

    def test_a_torn_final_fragment_is_skipped_not_raised(self) -> None:
        good = json.dumps(self._committed_record(), sort_keys=True)
        # A complete committed record, then a crash-torn final fragment (no trailing newline).
        self.log.write_text(good + "\n" + '{"state":"prepared","upgrade_i')
        records = launcher.read_authority_upgrade_records(self.base, self.run_id)
        self.assertEqual(len(records), 1, records)
        self.assertEqual(records[0]["state"], launcher.MIGRATION_COMMITTED)
        committed = launcher.read_authority_upgrades(self.base, self.run_id)
        self.assertEqual(len(committed), 1)
        # The skipped bytes are RECORDED as evidence, not silently dropped.
        quarantine = self.log.with_name(self.log.name + ".torn")
        self.assertTrue(quarantine.exists())
        self.assertIn("prepared", quarantine.read_text())

    def test_a_torn_prepared_tail_lets_reconciliation_proceed(self) -> None:
        prepared = json.dumps({**self._committed_record(), "state": launcher.MIGRATION_PREPARED},
                              sort_keys=True)
        # `prepared` committed to disk, then a torn second `prepared` fragment mid-append.
        self.log.write_text(prepared + "\n" + '{"state":"prepared","upgrad')
        # The read no longer raises: the committed-only view is empty (only a prepared exists),
        # and reconciliation can run rather than every read refusing forever.
        committed = launcher.read_authority_upgrades(self.base, self.run_id)
        self.assertEqual(committed, ())
        records = launcher.read_authority_upgrade_records(self.base, self.run_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["state"], launcher.MIGRATION_PREPARED)

    def test_a_complete_corrupt_record_still_raises(self) -> None:
        # A newline-TERMINATED but unparsable record is corruption, not a torn tail.
        self.log.write_text('{"state":"committed"}\n{not json}\n')
        with self.assertRaises(launcher.LauncherError) as raised:
            launcher.read_authority_upgrade_records(self.base, self.run_id)
        self.assertIn(launcher.STANDALONE_MIGRATION_REFUSED, str(raised.exception))


# =====================================================================================
# Item 4 -- explicit migration does not pre-publish the composition
# =====================================================================================
@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class Item4NoPrePublicationTests(unittest.TestCase):
    """[P2] `migrate_standalone_prompt_composition` no longer persists the composition before
    the two-phase writer.  At `bcd5c6d` a crash after that outer persist left the composition
    on disk with NO `prepared`, so the AUTOMATIC path (`_upgrade_legacy_authority_if_needed`)
    completed the upgrade as `composition_source=persisted, actor=""`, dropping the actor and
    reason and refusing the original migration's replay."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r10-prepub-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt").mkdir()

    def test_a_crash_before_prepared_leaves_no_composition_for_the_automatic_path(self) -> None:
        run_id = "run_prepub"
        _ledger, _target, composition = _replay_legacy_run_without_composition(self.base, run_id)
        digest = launcher.prompt_composition_digest(composition)
        # Crash the two-phase writer the instant it is entered -- BEFORE it writes its own
        # `prepared` and persists the composition (step 2).  At `bcd5c6d` the outer persist
        # already put the composition on disk; here nothing has.
        orig = launcher._write_upgraded_legacy_authority

        def _boom(*_a, **_k):
            raise RuntimeError("crash before prepared")
        launcher._write_upgraded_legacy_authority = _boom
        self.addCleanup(setattr, launcher, "_write_upgraded_legacy_authority", orig)
        with contextlib.suppress(RuntimeError):
            launcher.migrate_standalone_prompt_composition(
                self.base, run_id, composition=composition, actor="op", reason="audited")
        launcher._write_upgraded_legacy_authority = orig
        # THE PROOF: the composition was NOT pre-published under its digest, so the archive
        # the automatic path would bind does not exist -- loading it refuses MISSING rather
        # than returning a record with a dropped actor.  At `bcd5c6d` the outer persist wrote
        # that archive, so this load would RETURN the record (the bug).
        with self.assertRaises(launcher.LauncherError) as pre:
            launcher.load_standalone_prompt_composition(self.base, run_id, digest=digest)
        self.assertIn(launcher.STANDALONE_PROMPT_COMPOSITION_MISSING, str(pre.exception))
        # And the AUTOMATIC path still refuses rather than upgrading with actor="".
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.load_standalone_authority(self.base, run_id)
        self.assertIn(launcher.STANDALONE_PROMPT_COMPOSITION_MISSING, str(caught.exception))
        self.assertEqual(launcher.read_authority_upgrades(self.base, run_id), (),
                         "an upgrade was committed from a pre-published composition")

    def test_the_audited_migration_preserves_actor_and_reason_across_every_cut(self) -> None:
        real_append = launcher._durable_append
        real_write = launcher._durable_write
        self.addCleanup(setattr, launcher, "_durable_append", real_append)
        self.addCleanup(setattr, launcher, "_durable_write", real_write)

        class _Crash(Exception):
            pass

        for cut in ("after_prepared", "after_rebind", "after_committed"):
            with self.subTest(cut=cut):
                run_id = "run_mig" + cut.replace("_", "")
                (self.base / "wt").mkdir(exist_ok=True)
                _ledger, target, composition = _replay_legacy_run_without_composition(
                    self.base, run_id)
                digest = launcher.prompt_composition_digest(composition)

                def _append(path, text, _cut=cut):
                    real_append(path, text)
                    if _cut == "after_prepared" and '"state": "prepared"' in text:
                        raise _Crash(_cut)
                    if _cut == "after_committed" and '"state": "committed"' in text:
                        raise _Crash(_cut)

                def _write(path, text, _cut=cut):
                    real_write(path, text)
                    if _cut == "after_rebind" and Path(path) == target:
                        raise _Crash(_cut)
                launcher._durable_append = _append
                launcher._durable_write = _write
                with contextlib.suppress(_Crash):
                    launcher.migrate_standalone_prompt_composition(
                        self.base, run_id, composition=composition, actor="op",
                        reason="audited-why")
                launcher._durable_append = real_append
                launcher._durable_write = real_write
                # Reconcile (the read path) then REPLAY the identical migration.  Whatever
                # the cut, the committed record binds the digest AND preserves actor/reason.
                with contextlib.suppress(launcher.LauncherError):
                    launcher.load_standalone_authority(self.base, run_id)
                audit = launcher.migrate_standalone_prompt_composition(
                    self.base, run_id, composition=composition, actor="op",
                    reason="audited-why")
                self.assertEqual(audit["bound_prompt_composition_digest"], digest)
                self.assertEqual(audit["actor"], "op", cut)
                self.assertEqual(audit["reason"], "audited-why", cut)
                committed = launcher.read_authority_upgrades(self.base, run_id)
                self.assertEqual(len(committed), 1, f"{cut}: {committed}")
                self.assertEqual(committed[0]["actor"], "op")
                self.assertEqual(committed[0]["reason"], "audited-why")

    # -- Iteration 3, F3: after-PUBLISH / before-rebind crash preserves the original identity
    def test_a_crash_after_publish_before_rebind_preserves_the_original_actor_reason(self) -> None:
        """The reviewer's iteration-2 gap (`publish_crash.txt`): the two-phase writer's
        step (2) PERSISTS the composition, then a crash lands BEFORE step (3) re-binds the
        primary authority.  At iteration 2 the recovery rolled the original audited
        ``prepared`` attempt back and the AUTOMATIC upgrade then bound the already-published
        composition with ``composition_source=persisted, actor="", reason=""`` -- the original
        operator identity was dropped and the explicit replay refused.  The reconcile now
        ROLLS THE ORIGINAL ATTEMPT FORWARD when its composition is published: it completes the
        rebind under the prepared attempt's OWN identity, so NO path yields ``actor=""`` and
        the replay converges with the ORIGINAL actor/reason."""
        run_id = "run_pubcrash"
        _ledger, _target, composition = _replay_legacy_run_without_composition(self.base, run_id)
        digest = launcher.prompt_composition_digest(composition)
        real_persist = launcher.persist_standalone_prompt_composition
        self.addCleanup(setattr, launcher, "persist_standalone_prompt_composition", real_persist)

        class _Crash(Exception):
            pass

        def _persist_then_crash(*a, **k):
            real_persist(*a, **k)                    # the publish LANDS on disk
            raise _Crash("crash after publish, before rebind")
        launcher.persist_standalone_prompt_composition = _persist_then_crash
        with contextlib.suppress(_Crash):
            launcher.migrate_standalone_prompt_composition(
                self.base, run_id, composition=composition, actor="originalop",
                reason="originalreason")
        launcher.persist_standalone_prompt_composition = real_persist
        # Recovery through the production authority load (reconcile + automatic path).
        loaded = launcher.load_standalone_authority(self.base, run_id)
        self.assertEqual(loaded.get("prompt_composition_digest"), digest)
        committed = launcher.read_authority_upgrades(self.base, run_id)
        self.assertEqual(len(committed), 1, committed)
        self.assertEqual(committed[0]["actor"], "originalop", committed)
        self.assertEqual(committed[0]["reason"], "originalreason", committed)
        self.assertEqual(committed[0]["composition_source"], "audited_migration", committed)
        # NO record on ANY path is anonymous.
        for rec in launcher.read_authority_upgrade_records(self.base, run_id):
            self.assertNotEqual((rec.get("actor"), rec.get("composition_source")),
                                ("", "persisted"),
                                f"an anonymous automatic upgrade consumed the composition: {rec}")
        # The original explicit replay converges (idempotent), never refuses.
        audit = launcher.migrate_standalone_prompt_composition(
            self.base, run_id, composition=composition, actor="originalop",
            reason="originalreason")
        self.assertEqual(audit["actor"], "originalop")
        self.assertEqual(audit["reason"], "originalreason")

    # -- Iteration 3, F2: a crash-torn upgrade-log tail is HEALED (removed) before the next
    #    append, at both the prepared and committed cut points -----------------------------
    def test_a_torn_upgrade_tail_is_healed_before_the_next_append_and_read_recovers(self) -> None:
        """The reviewer's iteration-2 gap (`torn_replay.txt`): the reader only SKIPPED an
        unterminated final fragment in memory; the bytes stayed in the log, so the next
        reconciliation / recovery append concatenated its terminal record onto the fragment
        and forged a newline-TERMINATED corrupt line that every later read then refused
        forever.  Under the migration lock the reconcile now HEALS the tail -- quarantines it
        to ``.torn`` and truncates the log to its last newline -- BEFORE any append, so the
        subsequent append lands cleanly and the read recovers.  Locked at BOTH the
        ``prepared`` and ``committed`` cut points, through real reconciliation AND explicit
        replay (not a read-only view); a COMPLETE corrupt record still RAISES."""
        for cut in ("prepared", "committed"):
            with self.subTest(cut=cut):
                run_id = "run_torn" + cut
                (self.base / "wt").mkdir(exist_ok=True)
                _ledger, _target, composition = _replay_legacy_run_without_composition(
                    self.base, run_id)
                if cut == "committed":
                    # Drive a real COMPLETE upgrade first, then tear the tail after it.
                    launcher.migrate_standalone_prompt_composition(
                        self.base, run_id, composition=composition, actor="op",
                        reason="audited")
                else:
                    # A real PREPARED-only attempt: crash the two-phase writer right after it
                    # fsyncs its `prepared` record (before persist), leaving a complete
                    # `prepared` as the log's last line.
                    real_append = launcher._durable_append

                    class _C(Exception):
                        pass

                    def _append(path, text):
                        real_append(path, text)
                        if '"state": "prepared"' in text:
                            raise _C()
                    launcher._durable_append = _append
                    try:
                        with contextlib.suppress(_C):
                            launcher.migrate_standalone_prompt_composition(
                                self.base, run_id, composition=composition, actor="op",
                                reason="audited")
                    finally:
                        launcher._durable_append = real_append
                log = launcher._authority_upgrade_log_path(self.base, run_id)
                # The read BEFORE the tear parses (a complete log).
                launcher.read_authority_upgrade_records(self.base, run_id)
                # Tear the tail: append an UNTERMINATED fragment (a crash mid-write).
                with log.open("a", encoding="utf-8") as handle:
                    handle.write('{"state":"comm')
                # Reconciliation under the lock heals the tail, then the append/read recover.
                launcher.reconcile_authority_upgrades(self.base, run_id)
                healed = log.read_text(encoding="utf-8")
                self.assertTrue(healed.endswith("\n"), f"{cut}: torn tail not healed: {healed!r}")
                self.assertNotIn('{"state":"comm', healed, f"{cut}: fragment survived: {healed!r}")
                self.assertEqual(log.with_name(log.name + ".torn").read_text(), '{"state":"comm',
                                 f"{cut}: torn fragment not quarantined")
                # Reads recover permanently (no corrupt combined line).
                recs = launcher.read_authority_upgrade_records(self.base, run_id)
                self.assertTrue(recs, f"{cut}: {recs}")
                # And the explicit replay converges to a committed audited upgrade.
                audit = launcher.migrate_standalone_prompt_composition(
                    self.base, run_id, composition=composition, actor="op", reason="audited")
                self.assertEqual(audit["actor"], "op", cut)
                committed = launcher.read_authority_upgrades(self.base, run_id)
                self.assertEqual(len(committed), 1, f"{cut}: {committed}")

    def test_a_complete_corrupt_upgrade_record_still_raises_after_healing(self) -> None:
        """Healing removes ONLY an unterminated torn tail; a newline-TERMINATED corrupt
        record is corruption, not a torn tail, and every read still RAISES on it."""
        run_id = "run_corrupt"
        _replay_legacy_run_without_composition(self.base, run_id)
        log = launcher._authority_upgrade_log_path(self.base, run_id)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write('{"state":"committed","bad"\n')     # COMPLETE (newline) but corrupt
        launcher._heal_torn_upgrade_tail_locked(log)          # a complete line is NOT a torn tail
        self.assertEqual(log.read_text(encoding="utf-8"), '{"state":"committed","bad"\n',
                         "healing truncated a COMPLETE (newline-terminated) record")
        self.assertFalse(log.with_name(log.name + ".torn").exists(),
                         "a complete corrupt record was wrongly quarantined as torn")
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.read_authority_upgrade_records(self.base, run_id)
        self.assertIn(launcher.STANDALONE_MIGRATION_REFUSED, str(caught.exception))


# =====================================================================================
# Follow-up (b) -- the exit-evidence wait derives from post_exit_drain_budget_ms
# =====================================================================================
class FollowupBExitEvidenceBudgetTests(unittest.TestCase):
    """The `recover_handle` exit-evidence wait is derived from the run profile's
    `post_exit_drain_budget_ms`, not the fixed 3.5 s constant."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r10-budget-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.run_id = "run_budget"

    def _adapter(self):
        from scripts.deterministic_workflow.standalone_adapter import StandaloneAdapter
        return StandaloneAdapter(None, artifact_base=self.base, run_id=self.run_id)

    def _write_profile(self, drain_ms: int) -> None:
        # A FROZEN (absolute-worktree) profile mapping the production loader accepts.
        mapping = {
            "driver": "claude", "binary": "sh", "supported_range": [[1, 0, 0], [9, 0, 0]],
            "bin_dirs": ["/bin"], "worktree": str(self.base / "wt"),
            "readiness_records": [{"channel": "structured", "record_type": "system",
                                   "session_field": "session_id"}],
            "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
            "identity_flag": "--session-id",
            "timeouts": {"post_exit_drain_budget_ms": drain_ms}}
        (self.base / "wt").mkdir(parents=True, exist_ok=True)
        path = launcher.standalone_profile_path(self.base, self.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(mapping))

    def test_a_large_drain_budget_widens_the_exit_evidence_wait(self) -> None:
        self._write_profile(12_000)
        adapter = self._adapter()
        budget = adapter._exit_evidence_budget_ms(self.run_id, "inc1")
        self.assertEqual(budget, 12_000 + adapter.EXIT_EVIDENCE_MARGIN_MS,
                         "the exit-evidence wait did not track post_exit_drain_budget_ms")
        self.assertNotEqual(budget, adapter.EXIT_EVIDENCE_BUDGET_MS,
                            "the wait is still the fixed 3.5 s constant")

    def test_an_unresolvable_profile_falls_back_to_the_constant(self) -> None:
        adapter = self._adapter()
        budget = adapter._exit_evidence_budget_ms("run_missing", "inc1")
        self.assertEqual(budget, adapter.EXIT_EVIDENCE_BUDGET_MS)


if __name__ == "__main__":
    unittest.main()
