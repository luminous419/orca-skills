"""OS-37 BUGFIX round 10 (run_5855732a7f74) -- VERSIONED BY OS-48 (run_f820764749d6, W-F9).

OS-48 retires the round-10 finality premise (a negative holder enumeration authorises the
watcher release whose hangup is the proof).  The finality fact is now POSITIVE: the watcher
holds the owner slave reference, writes the in-band fence marker after `waitpid`, and the
supervisor drains TO THE MARKER (boundary N, fence `os48.capture_fence.v1`).  Items 1 / iter-2 /
iter-4 below are rewritten over the fence; the libproc enumeration is `LibprocDiagnosticsTests`
(a diagnostic, never an authority); `Item2SlaveHolderTriStateTests` keeps `_slave_holders` as a
diagnostic (`none_observed`, not `proven_absent`) with the non-positive `killpg` guard lock.
Items 3 / 4 and follow-up (b) are unaffected.  The original round-10 text follows for the record.

ORIGINAL: the consolidated follow-up review of `bcd5c6d`
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
# Item 1 -- macOS lost-tail, OS-48 form: the watcher HOLDS the owner slave reference and
# writes the in-band fence marker after its reap; the supervisor drains TO THE MARKER
# =====================================================================================
class Item1MacOSLostTailTests(unittest.TestCase):
    """[P1] On darwin a session leader's exit REVOKES the controlling tty and DISCARDS the
    master's unread tail (OS-48 [MEASURED probe_01/04]: the discard happens on the LAST slave
    close, ~0.5-0.7 s, even with a live ctty leader).  Round 10 answered with a deferred watcher
    exit + a holder proof; OS-48 (DESIGN topology A) answers positively: the watcher keeps ONE
    slave reference (nothing can be discarded while it is held), writes the fence marker
    ``<<OS48-FENCE nonce>>`` into the slave after `waitpid`, and the supervisor drains TO THE
    MARKER -- the boundary N is a positive fact in the capture, not the absence of a holder.

    # superseded by OS-48: the round-10 discriminator was `drain_handoff_fd` + `_await_drain_handoff`
    # (a deferred exit alone) and `ended == "hangup"`; OS-48 keeps the deferral for the TWO-PHASE
    # release but the finality fact is the marker (`ended == "marker"`), never a hangup."""

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
               capture: str | None = None, fence_nonce: str = "",
               supervisor_identity: dict | None = None):
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
            image="/bin/sh", capture=capture, fence_nonce=fence_nonce,
            supervisor_identity=supervisor_identity)
        self._sessions.append(session)
        return session, Path(str(sentinel)), Path(capture)

    def test_the_spawn_and_watch_provide_the_deferred_exit_machinery(self) -> None:
        """The DETERMINISTIC discriminator, OS-48 form: `spawn` hands the supervisor the
        drain-handoff write end (two-phase release), the watcher CONTROL socket (mediated signal
        delivery) and the minted FENCE NONCE the watcher will write; the pty module has the
        release / defer / orphan-finalize / bounded-marker primitives.  At `b9aecce` none of
        `control_fd` / `fence_nonce` / `request_release_1` / `_defer_for_release` /
        `_orphan_finalize` / `_write_marker_bounded` exist."""
        session, sentinel, cap = self._spawn(
            "printf '{\"is_error\":false}\\n'\nexit 0\n")
        self.assertIn("drain_handoff_fd", session)
        self.assertGreaterEqual(int(session["drain_handoff_fd"]), 0)
        self.assertIn("control_fd", session, "spawn does not provide the watcher control socket")
        self.assertGreaterEqual(int(session["control_fd"]), 0)
        self.assertRegex(str(session.get("fence_nonce") or ""), r"^[0-9a-f]{32}$")
        for name in ("request_release_1", "_signal_drain_handoff", "_defer_for_release",
                     "_orphan_finalize", "_write_marker_bounded", "request_watcher_signal"):
            self.assertTrue(hasattr(pty_supervisor, name), f"no OS-48 primitive {name}")
        deadline = time.time() + 15
        while not sentinel.exists() and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(sentinel.exists(), "the watcher never wrote the exit sentinel")
        # the marker is IN THE STREAM after the reap (the slave is still held, so it is readable)
        buf = b""
        deadline = time.time() + 5
        while capture_mod.find_marker(buf, session["fence_nonce"])[0] < 0 and time.time() < deadline:
            import select as _select
            if _select.select([session["master_fd"]], [], [], 0.1)[0]:
                with contextlib.suppress(OSError):
                    buf += os.read(session["master_fd"], 65536)
        self.assertGreaterEqual(capture_mod.find_marker(buf, session["fence_nonce"])[0], 0,
                                f"the watcher did not write the fence marker: {buf!r}")

    def test_the_supervisor_drain_preserves_the_final_tail_before_the_revoke(self) -> None:
        """A real `StandaloneSession.drain_after_exit` over a production-spawned pty: the agent
        writes a success record then a FINAL FAILURE record and exits; the supervisor's drain
        runs after a delay (a slow supervisor -- at `bcd5c6d` the watcher's revoke had already
        discarded the tail by then).  OS-48: the watcher holds the slave reference, so nothing
        is discarded; the drain ends AT THE MARKER, BOTH records are inside ``[0, N)`` and the
        fence is published `final`."""
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
            session_id=session.session_id, incarnation=session.incarnation, capture=cap,
            fence_nonce=session.fence_nonce,
            supervisor_identity=session._self_identity(capture_mod.OWNER_SUPERVISOR))
        session.pty = spawn
        session.record = {"pid": spawn["pid"], "pgid": spawn["pid"],
                          "captured_tty": _tty(spawn["slave_name"]),
                          "proc_start_ticks": pty_supervisor.proc_start_ticks(spawn["pid"]),
                          "boot_id": pty_supervisor.host_boot_id()}
        deadline = time.time() + 15
        while not sentinel.exists() and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(sentinel.exists())
        time.sleep(0.4)          # a slow supervisor: at bcd5c6d the revoke has fired by now
        drained = session.drain_after_exit(budget_ms=4000)
        self.assertEqual(drained["ended"], "marker", drained)
        self.assertEqual(drained["finality"], "capture_finalized", drained)
        raw = session.capture.raw()
        n = int(drained["offset_n"])
        prefix = raw[:n]
        self.assertIn(b'"is_error":true', prefix,
                      "the FINAL failure record was discarded before the boundary")
        self.assertIn(b'"is_error":false', prefix)
        span = capture_mod.marker_span(raw, session.fence_nonce)
        self.assertEqual((span[0], span[1]), (n, int(drained["marker_len"])), span)
        fence = capture_mod.read_capture_fence(session._fence_path(), fence=session.fence)
        self.assertEqual(fence["outcome"], capture_mod.EVIDENCE_FINAL, fence)
        self.assertEqual(fence["record"]["boundary"]["sha256_prefix"],
                         capture_mod.prefix_digest(raw, n))


# =====================================================================================
# Iteration 2 correction, OS-48 form -- NO hangup / EOF / holder enumeration ever
# establishes the proof; the in-band marker does
# =====================================================================================
from scripts.deterministic_workflow.standalone_runtime import (  # noqa: E402
    StandaloneSession, _stream_is_final)
from scripts.deterministic_workflow import standalone_journal as _journal_mod  # noqa: E402


class _WiredRoom(unittest.TestCase):
    """Shared fixture: a real `StandaloneSession` over a PRODUCTION-spawned pty (the OS-48
    spawn: owner-held slave reference, fence nonce, control socket)."""

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

    def _wired(self, agent_body: str, *, run_id: str, budget_ms: int = 1500,
               pump: bool = True):
        """A real `StandaloneSession` wired to a production-spawned pty running ``agent_body``
        (an ``sh`` script).  Returns ``(session, sentinel_path)``; with ``pump`` (default) the
        supervisor pumps -- exactly as `await_completion` does -- until the watcher has reaped
        the agent and written the sentinel (so `drain_after_exit`'s exit-proven precondition
        holds), the watcher still HOLDING its slave reference and deferring for release."""
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
            image="/bin/sh", capture=cap, fence_nonce=session.fence_nonce,
            supervisor_identity=session._self_identity(capture_mod.OWNER_SUPERVISOR))
        self._sessions.append(spawn)
        session.pty = spawn
        session.record = {"pid": spawn["pid"], "pgid": spawn["pid"], "sid": spawn["sid"],
                          "captured_tty": _tty(spawn["slave_name"]),
                          "proc_start_ticks": pty_supervisor.proc_start_ticks(spawn["pid"]),
                          "boot_id": pty_supervisor.host_boot_id()}
        if pump:
            deadline = time.time() + 15
            while not Path(str(sentinel)).exists() and time.time() < deadline:
                session.pump(timeout_ms=20)
            self.assertTrue(Path(str(sentinel)).exists(), "the watcher never wrote the sentinel")
            session.pump(timeout_ms=50)
        return session, Path(str(sentinel))

    def _fence(self, session) -> dict:
        return capture_mod.read_capture_fence(session._fence_path(), fence=session.fence)

    def _read_more(self, session, needle: bytes, *, seconds: float) -> bytes:
        """Keep reading the (still-alive) master into the capture until ``needle`` is present
        or ``seconds`` elapse; returns the raw capture."""
        deadline = time.time() + seconds
        while needle not in session.capture.raw() and time.time() < deadline:
            session.pump(timeout_ms=50)
        return session.capture.raw()

    def _read_until_marker(self, session, *, seconds: float) -> None:
        deadline = time.time() + seconds
        while (capture_mod.find_marker(session.capture.raw(), session.fence_nonce)[0] < 0
               and time.time() < deadline):
            session.pump(timeout_ms=50)

    @staticmethod
    def _offsets(raw: bytes, needle: bytes) -> list[int]:
        out, i = [], raw.find(needle)
        while i >= 0:
            out.append(i)
            i = raw.find(needle, i + 1)
        return out


class Iteration2FinalityBoundaryTests(_WiredRoom):
    """[P1] The controlling invariant, OS-48 form (DESIGN I-1): no hangup, EOF, quiet window
    or holder enumeration ever establishes the proof the supervisor consumes -- the pinned
    root's exit (`waitpid` by its parent) plus the in-band fence marker written by the
    owner-held slave reference DO.  The settlement window is ``[baseline, N)``; every byte a
    retained-slave descendant writes after the marker is DIAGNOSTIC (after N) and can neither
    fail nor complete the dispatch; a hangup without the marker is `boundary_unproven`.

    # superseded by OS-48: the round-10 `stream_end_unproven` / `watcher_exit_unproven` /
    # `proven_absent`-gated release locks (`:278-537` at b9aecce) -- their premise (a negative
    # holder scan authorises the release; the release's hangup is the proof) is retired."""

    # -- Lock 1: retained-slave descendant + late final failure record -------------------
    def test_a_retained_slave_descendant_late_record_lands_after_the_boundary(self) -> None:
        """The agent emits a success record and EXITS; a descendant it forked KEEPS the slave,
        stays quiet, then writes a FAILURE record and holds the slave for seconds.  OS-48: the
        marker is written when the pinned root is reaped, so the late record lands AFTER N --
        diagnostic, never part of the settlement window -- and the drain ends at the marker,
        never `budget`.  The slave is still held by the watcher, so the late bytes are
        retained (not discarded) and readable after N.  At b9aecce this was
        `stream_end_unproven` (the holder blocked the release)."""
        trigger = self.room / "retain.go"
        # HANDSHAKE (OS-48 F-007): the descendant writes only once the fence is bound.
        session, _sent = self._wired(
            "printf '{\"type\":\"result\",\"is_error\":false}\\n'\n"
            "( while [ ! -e %s ]; do sleep 0.01; done; printf '{\"type\":\"result\",\"is_error\":true}\\n'; sleep 5 ) &\n"
            "exit 0\n" % trigger, run_id="run_r10i2_retain", budget_ms=1500)
        drained = session.drain_after_exit(budget_ms=1500)
        self.assertEqual(drained.get("ended"), "marker", drained)
        self.assertTrue(_stream_is_final(drained), drained)
        n = int(drained["offset_n"])
        raw = session.capture.raw()
        self.assertIn(b'"is_error":false', raw[:n])
        self.assertNotIn(b'"is_error":true', raw[:n],
                         "a post-reap descendant record leaked into the settlement window")
        trigger.write_text("go")
        raw = self._read_more(session, b'"is_error":true', seconds=3.0)
        late = self._offsets(raw, b'"is_error":true')
        self.assertTrue(late, "the retained holder's late record was discarded (slave not held)")
        self.assertGreaterEqual(min(late), n + int(drained["marker_len"]), (n, late))
        self.assertEqual(self._fence(session)["outcome"], capture_mod.EVIDENCE_FINAL)
        self.assertEqual(int(self._fence(session)["record"]["boundary"]["offset_n"]), n)

    # -- Lock 5: positive path, both records before N, supervisor-owned fence ------------
    def test_the_positive_path_publishes_the_supervisor_fence_with_both_records(self) -> None:
        """No descendant: both records precede the marker; the drain ends at the marker; the
        fence is `final`, owned by generation g1 = THIS supervisor, its digest is the prefix
        digest; the two-phase release then retains the (empty) diagnostic tail and publishes
        `release.<inc>` `final` with R after N."""
        session, _sent = self._wired(
            "printf '{\"type\":\"result\",\"is_error\":false}\\n'\n"
            "printf '{\"type\":\"result\",\"is_error\":true}\\n'\n"
            "exit 0\n", run_id="run_r10i2_pos", budget_ms=4000)
        drained = session.drain_after_exit(budget_ms=4000)
        self.assertEqual(drained.get("ended"), "marker", drained)
        self.assertTrue(_stream_is_final(drained), drained)
        n = int(drained["offset_n"])
        raw = session.capture.raw()
        self.assertIn(b'"is_error":false', raw[:n])
        self.assertIn(b'"is_error":true', raw[:n])
        fence = self._fence(session)
        self.assertEqual(fence["outcome"], capture_mod.EVIDENCE_FINAL, fence)
        record = fence["record"]
        self.assertEqual(record["owner"]["owner_role"], capture_mod.OWNER_SUPERVISOR)
        self.assertEqual(int(record["owner"]["generation"]), 1)
        self.assertEqual(int(record["owner"]["owner"]["pid"]), os.getpid())
        self.assertEqual(record["boundary"]["sha256_prefix"], capture_mod.prefix_digest(raw, n))
        released = session._release_two_phase()
        self.assertEqual(released["state"], capture_mod.EVIDENCE_FINAL, released)
        self.assertGreater(int(released["offset_r"]), n)
        rel = capture_mod.read_release_record(session._release_path(), fence=session.fence)
        self.assertEqual(rel["outcome"], capture_mod.EVIDENCE_FINAL, rel)
        self.assertEqual(int(rel["record"]["retained_tail_bytes"]), 0, rel)

    # -- Lock 4: another writer's record at the fence path is never overwritten or accepted
    def test_a_forged_fence_at_the_path_is_never_overwritten_and_never_accepted(self) -> None:
        """A SECOND component publishes a fence (bogus digest / boundary) at the fence path
        BEFORE the supervisor finalizes.  OS-48 publication is link-EXCLUSIVE: the supervisor
        never overwrites it; it reads it and VERIFIES it against the capture, and the mismatch
        is a NAMED non-success (`fence_mismatch`) -- the forged record does not stand as the
        proof and no success is possible over it."""
        session, _sent = self._wired(
            "printf '{\"type\":\"result\",\"is_error\":false}\\n'\n"
            "exit 0\n", run_id="run_r10i2_forge", budget_ms=4000)
        forged = capture_mod.make_capture_fence(
            fence=session.fence, emitter={"pid": 1, "start_id": 1, "boot_id": "x"},
            emitter_pgid=1, offset_n=7, marker_len=43, marker_nonce=session.fence_nonce,
            sha256_prefix="deadbeef" * 8, tail_bytes_at_publish=0, exit_how="exit_sentinel",
            exit_code=0, reaped_by=None,
            owner={"owner_role": capture_mod.OWNER_EXIT_WATCHER, "generation": 1,
                   "owner": {"pid": 1, "start_id": 1, "boot_id": "x"}},
            evidence_source="forged", provenance=["forged"], published_at="now")
        capture_mod.write_capture_fence(session._fence_path(), forged)
        before = Path(os.fsdecode(session._fence_path())).read_bytes()
        drained = session.drain_after_exit(budget_ms=4000)
        self.assertEqual(drained.get("ended"), "marker", drained)
        self.assertFalse(_stream_is_final(drained), drained)
        self.assertEqual(drained.get("outcome"), capture_mod.OUTCOME_FENCE_MISMATCH, drained)
        self.assertEqual(Path(os.fsdecode(session._fence_path())).read_bytes(), before,
                         "the forged fence was overwritten (publication must be exclusive)")

    # -- Lock 6 (C3): the watcher's death AFTER the marker does not disturb the boundary --
    def test_a_watcher_death_after_the_marker_does_not_disturb_the_boundary(self) -> None:
        """The exit watcher is released (closes its slave reference and exits) BEFORE the
        supervisor's finalizing drain.  Round 10 called this `watcher_exit_unproven` (the
        hangup arrived with the watcher gone).  OS-48 (cut C3): the marker was written and
        captured BEFORE the watcher went, so the boundary is intact and the fence publishes
        from it; what the watcher's death costs is the RELEASE protocol -- no RELEASE marker
        can ever be written, so the release record is NAMED `diagnostic_tail_unaccounted` /
        `release_record_missing`, never silently `final`."""
        session, _sent = self._wired(
            "printf '{\"type\":\"result\",\"is_error\":false}\\n'\n"
            "printf '{\"type\":\"result\",\"is_error\":true}\\n'\n"
            "exit 0\n", run_id="run_r10i2_wexit", budget_ms=4000)
        self._read_until_marker(session, seconds=5.0)
        self.assertGreaterEqual(capture_mod.find_marker(session.capture.raw(), session.fence_nonce)[0],
                                0, "the marker never arrived while held")
        leader = int(session.pty["leader_pid"])
        pty_supervisor._signal_drain_handoff(session.pty)     # release-2 without release-1
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                done, _ = os.waitpid(leader, os.WNOHANG)
            except ChildProcessError:
                break
            if done == leader:
                break
            time.sleep(0.02)
        time.sleep(0.2)                          # let any revoke settle
        drained = session.drain_after_exit(budget_ms=4000)
        self.assertEqual(drained.get("ended"), "marker", drained)
        self.assertTrue(_stream_is_final(drained), drained)
        n = int(drained["offset_n"])
        raw = session.capture.raw()
        self.assertIn(b'"is_error":true', raw[:n])
        released = session._release_two_phase()
        self.assertNotEqual(released["state"], capture_mod.EVIDENCE_FINAL, released)
        self.assertEqual(released["outcome"], capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED)
        self.assertEqual(self._fence(session)["outcome"], capture_mod.EVIDENCE_FINAL)

    # -- Lock 7 (L-01/L-12 in the OS-48 suites): an EOF without the marker proves nothing --
    def test_an_eof_without_the_marker_authorises_nothing(self) -> None:
        """# superseded by OS-48: round 10's `test_an_agent_hangup_while_the_watcher_is_held_is_proven`
        # made a hangup-with-live-watcher the proof.  OS-48: an EOF / hangup is NEVER the proof.
        The drain's master is pointed at an already-hung-up pipe (an immediately readable EOF)
        before the marker was ever captured: the drain ends `master_unreadable`, the outcome is
        `boundary_unproven`, no fence is published, and `_stream_is_final` is False -- on every
        platform, with the real watcher genuinely alive and holding the slave."""
        session, _sent = self._wired(
            "printf '{\"type\":\"result\",\"is_error\":false}\\n'\nexit 0\n",
            run_id="run_r10i2_eof", budget_ms=1500, pump=False)
        real_master = int(session.pty["master_fd"])
        hup_r, hup_w = os.pipe()
        os.close(hup_w)
        def _close_hup() -> None:
            with contextlib.suppress(OSError):
                os.close(hup_r)
        self.addCleanup(_close_hup)
        session.pty["master_fd"] = hup_r
        try:
            drained = session.drain_after_exit(budget_ms=1500)
        finally:
            session.pty["master_fd"] = real_master
        self.assertEqual(drained.get("ended"), "master_unreadable", drained)
        self.assertEqual(drained.get("outcome"), capture_mod.OUTCOME_BOUNDARY_UNPROVEN, drained)
        self.assertFalse(_stream_is_final(drained), drained)
        self.assertEqual(self._fence(session)["outcome"], "absent", self._fence(session))

    # -- Lock 2: a descendant writing around the marker is ORDERED by the marker ---------
    def test_a_descendant_writing_around_the_marker_is_ordered_by_the_marker(self) -> None:
        """A descendant that keeps the slave and writes right around the reap is ordered by
        the tty's own byte order: its record is either wholly inside ``[0, N)`` (it preceded
        the marker -- then it is in the settlement window, honestly) or wholly after the
        marker (diagnostic).  Either way the drain ends at the marker and the window is
        positively bounded; at b9aecce this was `budget` + `stream_end_unproven`."""
        session, _sent = self._wired(
            "printf '{\"type\":\"result\",\"is_error\":false}\\n'\n"
            "( sleep 0.15; printf '{\"type\":\"result\",\"is_error\":true}\\n'; sleep 5 ) &\n"
            "exit 0\n", run_id="run_r10i2_race", budget_ms=1200)
        drained = session.drain_after_exit(budget_ms=1200)
        self.assertEqual(drained.get("ended"), "marker", drained)
        self.assertTrue(_stream_is_final(drained), drained)
        n, mlen = int(drained["offset_n"]), int(drained["marker_len"])
        raw = self._read_more(session, b'"is_error":true', seconds=3.0)
        rec = b'{"type":"result","is_error":true}\n'
        for off in self._offsets(raw, rec):
            self.assertTrue(off + len(rec) <= n or off >= n + mlen,
                            f"a record straddles the boundary: off={off} n={n}")

    # -- Lock 8: a DETACHED (setsid) slave holder -- invisible to `ps -t` -- changes nothing
    def test_a_detached_setsid_slave_holder_late_record_lands_after_the_boundary(self) -> None:
        """The reviewer's iteration-2 counterexample (`detached_holder2.txt`): the agent emits
        a success record and forks a child that calls ``setsid()`` while KEEPING the pty slave
        as its 0/1/2, then exits; the child writes a late FAILURE record.  Round 10 needed a
        COMPLETE descriptor enumeration to see the off-tty holder.  OS-48 needs NO enumeration:
        the marker is written at the root's reap, the late record lands after N, the fence is
        `final` over ``[0, N)`` and -- because the watcher holds the slave -- the child's late
        write is retained, never EIO'd by a premature revoke."""
        agent = self.room / "detached_agent.py"
        pidfile = self.room / "holder.pid"
        agent.write_text(
            "import os, time, signal, sys\n"
            "sys.stdout.write('{\"type\":\"result\",\"is_error\":false}\\n'); sys.stdout.flush()\n"
            "if os.fork() == 0:\n"
            "    os.setsid()\n"                       # leave the tty session, KEEP slave 0/1/2
            "    signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
            "    open(%r, 'w').write(str(os.getpid()))\n" % str(pidfile) +
            "    while not os.path.exists(%r): time.sleep(0.005)\n" % str(self.room / "detached.go") +
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
        self.assertEqual(drained.get("ended"), "marker", drained)
        self.assertTrue(_stream_is_final(drained), drained)
        n = int(drained["offset_n"])
        self.assertNotIn(b'"is_error":true', session.capture.raw()[:n])
        (self.room / "detached.go").write_text("go")     # HANDSHAKE (F-007): write after N is bound
        raw = self._read_more(session, b'"is_error":true', seconds=3.0)
        late = self._offsets(raw, b'"is_error":true')
        self.assertTrue(late, "the detached holder's late record was lost (premature revoke)")
        self.assertGreaterEqual(min(late), n + int(drained["marker_len"]))
        os.kill(holder, 0)                            # never revoked out from under it
        self.assertEqual(self._fence(session)["outcome"], capture_mod.EVIDENCE_FINAL)


# =====================================================================================
# Iteration 4/5 libproc enumeration, OS-48 form -- a DIAGNOSTIC, never an authority
# =====================================================================================
class LibprocDiagnosticsTests(_WiredRoom):
    """# superseded by OS-48: `Iteration4LibprocAuthorityTests` -- the "COMPLETE, fail-closed
    # slave-descriptor authority" whose `proven_absent` gated the watcher release.

    OS-48 (ANALYSIS F0, DESIGN I-1/§2.6): a negative whole-process-table / descriptor scan is
    never evidence of anything (`proc_listallpids` / `PROC_PIDLISTFDS` / `PROC_PIDFDVNODEPATHINFO`
    are non-atomic; fork / SCM_RIGHTS / close between them defeat every negative).  The
    enumeration survives as a DIAGNOSTIC: its states are `present` / `unreadable` /
    `none_observed` (the word "proven" does not occur), denied / short / stale reads are
    `unreadable` BY NAME, and no decision function reaches it (the last lock here).  The same
    iter-5 constructions (F1-F4) are kept as diagnostic-layer regressions: they must still
    NAME what they saw."""

    def _clean_slave(self):
        session, _sent = self._wired("printf 'x\\n'\nexit 0\n", run_id="run_r10i4_clean",
                                     budget_ms=1500)
        return session

    def _holder_scan(self) -> dict:
        """Scan the holder fixture's slave, excluding the agent and the watcher (which HOLDS the
        owner reference by design)."""
        return pty_supervisor.slave_device_holders(self._holder_slave,
                                                   exclude_pids=self._holder_exclude)

    def _scan(self, session) -> dict:
        return pty_supervisor.slave_device_holders(
            session.pty["slave_name"],
            exclude_pids=(int(session.record["pid"]), int(session.pty["leader_pid"])))

    def test_the_watcher_holds_the_owner_slave_reference(self) -> None:
        """OS-48 G1 inverts the round-10 spawn invariant: the exit watcher (session leader) HOLDS
        ONE slave reference (nothing can be discarded while it is held; it writes the marker
        through it).  The diagnostic must SEE the leader among the holders when it is not
        excluded."""
        if sys.platform != "darwin":
            return
        session = self._clean_slave()
        leader = int(session.pty["leader_pid"])
        holders = pty_supervisor.slave_device_holders(session.pty["slave_name"],
                                                      exclude_pids=(int(session.record["pid"]),))
        self.assertIn(leader, holders.get("holders") or [],
                      f"the watcher does not hold the owner slave reference: {holders}")
        self.assertEqual(holders.get("state"), "present")

    def test_a_clean_slave_is_never_positively_absent(self) -> None:
        """A clean slave (no same-uid holder besides the excluded watcher) on a host full of
        OTHER-uid processes whose fd listings the kernel denies: the diagnostic is `unreadable`
        with every denied listing NAMED (`listing_denied`) -- never `proven_absent`, never a
        silent `other_uid` skip -- and holds no holder."""
        if sys.platform != "darwin":
            return
        session = self._clean_slave()
        session.drain_after_exit(budget_ms=1500)
        holders = self._scan(session)
        self.assertNotEqual(holders.get("state"), "present", holders)
        self.assertNotIn("proven", str(holders.get("state")))
        self.assertIn(holders.get("state"), ("unreadable", "none_observed"), holders)
        self.assertFalse(holders.get("holders"), holders)
        if holders["state"] == "unreadable":
            # superseded by OS-48 (i3/i4 F-004, versioned in i8): the fd cross-check names its
            # own unreadable states -- an fd table beyond the bounded walk (`fd_walk_unbounded`),
            # an unreadable table size, a partial fill -- all honest "unreadable", never clean
            self.assertTrue(all(u.get("errno") in ("listing_denied", "fd_denied",
                                                     "stale_revoked_fd",
                                                     "fd_table_never_stabilised",
                                                     "fd_walk_unbounded",
                                                     "fd_table_size_unreadable",
                                                     "listing_partial_fill")
                                or isinstance(u.get("errno"), int)
                                for u in holders["unenumerable"]), holders)
            self.assertTrue(any(u.get("errno") == "listing_denied"
                                for u in holders["unenumerable"]), holders)

    def test_the_pid_listing_returns_every_entry_not_a_quarter(self) -> None:
        """F1 (kept, diagnostic layer): `proc_listallpids` returns the number of PID ENTRIES;
        the listing must return the whole table, not a quarter of it."""
        if sys.platform != "darwin":
            return
        n = pty_supervisor._LIBPROC.proc_listallpids(None, 0)
        buf = (ctypes.c_int32 * (n + 256))()
        got = pty_supervisor._LIBPROC.proc_listallpids(buf, ctypes.sizeof(buf))
        native_nonzero = len([1 for i in range(len(buf)) if buf[i] > 0])
        pids, reason = pty_supervisor._libproc_list_all_pids()
        self.assertIsNotNone(pids, f"listing failed: {reason}")
        self.assertGreater(len(pids), (got // 4) + 8,
                           f"the listing looks byte-length-divided: {len(pids)} vs got//4={got // 4}")
        self.assertGreaterEqual(len(pids), int(native_nonzero * 0.6),
                                f"the listing dropped most of the table: {len(pids)} of {native_nonzero}")

    def test_a_quarter_count_listing_misses_a_tail_holder_MUTATION(self) -> None:
        """F1 mutation (diagnostic layer): a quarter-count listing drops a live holder past the
        prefix -- the diagnostic then fails to NAME it (`present` is missed).  That the real
        full-count listing names it is the lock; nothing downstream depends on either."""
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
                return keep, None
            return [p for p in keep if p != holder], None
        pty_supervisor._libproc_list_all_pids = _quarter
        self.addCleanup(setattr, pty_supervisor, "_libproc_list_all_pids", real)
        holders = self._holder_scan()
        self.assertNotIn(holder, holders.get("holders") or [], holders)
        self.assertNotEqual(holders.get("state"), "present", holders)

    def test_a_holder_with_denied_identity_is_still_caught_by_fd_inspection(self) -> None:
        """F2 (kept): a live same-uid setsid holder whose IDENTITY read is denied is still
        NAMED `present` through its fd listing."""
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
        holders = self._holder_scan()
        self.assertEqual(holders.get("state"), "present", holders)
        self.assertIn(holder, holders.get("holders") or [], holders)

    def test_a_denied_fd_listing_is_unreadable_not_absence(self) -> None:
        """# superseded by OS-48: `test_a_denied_fd_listing_is_other_uid_not_unenumerable` read a
        # denied listing as the `other_uid` bucket that "cannot hold the slave".
        OS-48 I-2: a denied read is UNREADABLE evidence, named `listing_denied` for that pid;
        the diagnostic is `unreadable`, never an absence."""
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
        holders = self._scan(session)
        self.assertNotIn(me, holders.get("other_uid") or [], holders)
        rows = [u for u in holders.get("unenumerable") or () if u.get("pid") == me]
        self.assertEqual([u.get("errno") for u in rows], ["listing_denied"], holders)
        self.assertEqual(holders.get("state"), "unreadable", holders)

    def test_a_relocated_slave_is_caught_never_absent(self) -> None:
        """F3 (kept): a holder that `dup2`-relocates the slave DURING the scan is re-scanned
        over a FRESH listing -> `present` (or `unreadable` if the table never settles), never
        `none_observed`."""
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
        holders = self._holder_scan()
        self.assertNotEqual(holders.get("state"), "none_observed", holders)
        self.assertIn(holders.get("state"), ("present", "unreadable"), holders)

    def test_treating_a_relocation_ebadf_as_gone_is_caught_MUTATION(self) -> None:
        """F3 mutation (diagnostic layer): EBADF-as-gone drops the relocating holder before
        the fresh listing reveals the relocated slave, so it is never NAMED."""
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
        holders = self._holder_scan()
        self.assertNotIn(holder, holders.get("holders") or [], holders)
        self.assertNotEqual(holders.get("state"), "present", holders)

    def test_a_bystander_that_closes_a_fd_once_is_rescanned(self) -> None:
        """F3 positive (kept): a BYSTANDER that closes a vnode fd once during the scan is
        re-scanned over a fresh listing and, being clean and stable, is neither a holder nor
        `unenumerable`."""
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
        holders = self._scan(session)
        self.assertNotEqual(holders.get("state"), "present", holders)
        self.assertFalse(any(u.get("pid") == me for u in holders.get("unenumerable") or ()), holders)

    def test_a_race_exited_candidate_is_gone_not_unreadable(self) -> None:
        """Kept: a candidate whose LISTING vanishes (ESRCH) has exited -- it is `gone`
        (diagnostic), not `unenumerable`."""
        if sys.platform != "darwin":
            return
        session = self._clean_slave()
        session.drain_after_exit(budget_ms=1500)
        me = os.getpid()
        real = pty_supervisor._libproc_list_vnode_fds

        def _vanish(pid, _real=real):
            if pid == me:
                return None, errno.ESRCH
            return _real(pid)
        pty_supervisor._libproc_list_vnode_fds = _vanish
        self.addCleanup(setattr, pty_supervisor, "_libproc_list_vnode_fds", real)
        holders = self._scan(session)
        self.assertIn(me, holders.get("gone") or [], holders)
        self.assertFalse(any(u.get("pid") == me for u in holders.get("unenumerable") or ()), holders)

    def test_a_revoked_fd_enoent_is_stale_not_gone(self) -> None:
        """The twin (DESIGN W-F9; ANALYSIS probe_03 part 2): a per-fd ENOENT on a LIVE process is
        a REVOKED (stale) vnode, never "the process is gone" -- named `stale_revoked_fd`, the
        diagnostic `unreadable`, the pid NOT in `gone`."""
        if sys.platform != "darwin":
            return
        session = self._clean_slave()
        session.drain_after_exit(budget_ms=1500)
        me = os.getpid()
        real = pty_supervisor._libproc_fd_devino

        def _stale(pid, fd, _real=real):
            if pid == me:
                return None, errno.ENOENT
            return _real(pid, fd)
        pty_supervisor._libproc_fd_devino = _stale
        self.addCleanup(setattr, pty_supervisor, "_libproc_fd_devino", real)
        holders = self._scan(session)
        self.assertNotIn(me, holders.get("gone") or [], holders)
        rows = [u for u in holders.get("unenumerable") or () if u.get("pid") == me]
        self.assertEqual([u.get("errno") for u in rows], ["stale_revoked_fd"], holders)
        self.assertEqual(holders.get("state"), "unreadable", holders)

    def test_a_short_per_fd_read_is_unenumerable(self) -> None:
        """F4 (kept): a positive SHORT `proc_pidfdinfo` return is never decoded; the holder is
        `unenumerable` and the diagnostic `unreadable`."""
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
        holders = self._holder_scan()
        self.assertEqual(holders.get("state"), "unreadable", holders)
        self.assertTrue(any(u.get("pid") == holder for u in holders.get("unenumerable") or ()),
                        f"the short-read holder was not named unenumerable: {holders}")

    def test_accepting_a_short_read_is_caught_MUTATION(self) -> None:
        """F4 mutation (diagnostic layer): a decode that accepts a short read yields garbage
        that fails the match, so the live holder is never NAMED."""
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
        holders = self._holder_scan()
        self.assertNotIn(holder, holders.get("holders") or [], holders)

    def test_a_failed_process_listing_is_unreadable(self) -> None:
        """Kept: if `proc_listallpids` itself fails, the diagnostic is `unreadable`."""
        if sys.platform != "darwin":
            return
        holder = self._spawn_setsid_holder()
        real = pty_supervisor._LIBPROC.proc_listallpids

        def _fail(*_a):
            ctypes.set_errno(errno.EPERM)
            return -1
        pty_supervisor._LIBPROC.proc_listallpids = _fail
        self.addCleanup(setattr, pty_supervisor._LIBPROC, "proc_listallpids", real)
        holders = self._holder_scan()
        self.assertEqual(holders.get("state"), "unreadable", holders)
        _ = holder

    def test_the_enumeration_is_not_on_any_decision_path(self) -> None:
        """# superseded by OS-48: `test_skipping_a_holder_pid_is_caught` proved the finality lock
        # DEPENDED on the enumeration.  OS-48 proves the opposite (DESIGN L-14 shape): both
        enumerations are replaced by functions that RAISE; the production drain, fence
        publication and two-phase release still complete `final` -- no decision reaches them."""
        def _boom(*_a, **_k):
            raise AssertionError("a decision path consulted the holder enumeration")
        for name in ("slave_device_holders", "_slave_holders", "_slave_holder_state",
                     "_slave_device_fd_holders"):
            if hasattr(pty_supervisor, name):
                real = getattr(pty_supervisor, name)
                setattr(pty_supervisor, name, _boom)
                self.addCleanup(setattr, pty_supervisor, name, real)
        session, _sent = self._wired(
            "printf '{\"type\":\"result\",\"is_error\":false}\\n'\nexit 0\n",
            run_id="run_r10i4_nodecision", budget_ms=3000)
        drained = session.drain_after_exit(budget_ms=3000)
        self.assertEqual(drained.get("ended"), "marker", drained)
        self.assertTrue(_stream_is_final(drained), drained)
        released = session._release_two_phase()
        self.assertEqual(released["state"], capture_mod.EVIDENCE_FINAL, released)

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
        self._holder_exclude = (int(session.record["pid"]), int(session.pty["leader_pid"]))
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
        self._holder_exclude = (int(session.record["pid"]), int(session.pty["leader_pid"]))
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
    `_slave_holder_state` yields `present` / `none_observed` / `unreadable` (OS-48: a
    DIAGNOSTIC -- `none_observed` replaces `proven_absent`; the non-positive `killpg` guard
    lock is retained, DESIGN L-11).  At `bcd5c6d`
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
        self.assertEqual(pty_supervisor._slave_holder_state(holders), "none_observed")

    def test_a_complete_empty_probe_is_none_observed(self) -> None:
        """# superseded by OS-48: `test_a_complete_empty_probe_is_proven_absent` -- an empty
        # tty-scoped probe is `none_observed` (diagnostic), never a positive absence."""
        master = self._real_master()
        holders = pty_supervisor._slave_holders(
            master, self._slave_name,
            tcgetpgrp=lambda _fd: 999999,
            killpg=lambda _p, _s: (_ for _ in ()).throw(ProcessLookupError()),
            isdir=lambda _p: False)
        self.assertEqual(pty_supervisor._slave_holder_state(holders), "none_observed")

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
