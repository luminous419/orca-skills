"""OS-48 locks for REVIEW_IMPLEMENTATION_iteration5.md F-010 (+ the worker's own adversarial
pass over discovery) and F-011, each through the REAL watcher / runtime or the real lock.

F-010 -- a POSITIVE kernel fork whose child cannot be attributed is never silently clean:

* the reviewer's registered-fork / parent-exit cut (`probe_registered_fork_after_root_exit_real`):
  the root is gated until the watcher's member registration is complete, the first discovery
  is delayed until the root's positive exit, the kernel delivers NOTE_FORK|NOTE_EXIT in one
  event, the detached helper is reparented before any walk -> `descendants_unknown` with a
  durable `fork_unattributed` record carrying the parent identity and the event, a named
  `descendants_unreaped` row, the helper unsignalled, the fence valid; the adopting successor
  reads the same residual (recovery path);
* the Linux equivalent of that cut: no fork events exist, but the watcher is a SUBREAPER, so
  the orphaned helper reparents to it and is attributed POSITIVELY (`subreaper_reparent`) --
  the residual names it alive;
* the readable control with an ACKNOWLEDGED ordering gate (the root exits only after the
  ledger names the helper): positive member, discovery readable, zero fork uncertainty;
* every other silent-drop path found by the adversarial pass is a named record: a member whose
  fork watch could not be registered (`fork_watch_unregistered`), no kqueue at all
  (`fork_watch_unavailable`), an unreadable kqueue read (`fork_events_unreadable`), a
  positively attributed descendant the ledger ceiling would not hold (`member_ceiling_exceeded`),
  a parent member no identity source will read (`parent_identity_unreadable`).

F-011 -- the OS-42 historical-artifact lock protects every SETTLED run whether or not git
tracks it and excludes only the ACTIVE run by its own positive marker (an unreleased OS-44
coordinator-session binding); the mutation controls live in `test_os42_artifacts` and are
re-asserted here on the real tree.
"""
from __future__ import annotations

import errno
import json
import os
import select
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
from scripts.test_os48_review_i4_locks import _ROOT, _HelperCase  # noqa: E402

DARWIN_ONLY = unittest.skipUnless(sys.platform == "darwin", "OS-48 libproc evidence seams are darwin's (probe_03)")
LINUX_ONLY = unittest.skipUnless(sys.platform == "linux", "the /proc discovery walk is Linux's")

#: The reviewer's F-010 root: it waits for the watcher's REGISTRATION gate before forking
#: (so the kqueue watch on it exists), forks the detached helper, waits for its "ready" byte,
#: emits its bound completion record and exits AT ONCE (no linger, no ack gate).
_GATED_ROOT = """import os,time,json,signal
while not os.path.exists(%r): time.sleep(.005)
r,w=os.pipe()
if os.fork()==0:
    os.close(r);os.setsid();signal.signal(signal.SIGHUP,signal.SIG_IGN)
    open(%r,"w").write(json.dumps(dict(pid=os.getpid())))
    os.write(w,b"r");os.close(w);time.sleep(30);os._exit(0)
os.close(w);os.read(r,1);os.close(r)
os.write(1,(json.dumps(dict(type="result",is_error=False,session_id=os.environ["OS48_TEST_SID"]))+"\\n").encode());os._exit(0)
"""


class _RecordingKqueue:
    """The reviewer's forwarding kqueue: records every event the kernel returned, unmodified."""
    real_kqueue = getattr(select, "kqueue", None)          # darwin only; never touched on Linux

    def __init__(self, log: Path):
        self.real = self.real_kqueue()
        self.log = log

    def fileno(self):
        return self.real.fileno()

    def close(self):
        return self.real.close()

    def control(self, *args, **kwargs):
        got = self.real.control(*args, **kwargs)
        for ev in got:
            with self.log.open("a") as f:
                f.write(json.dumps({"ident": int(ev.ident), "fflags": int(ev.fflags),
                                    "fork": bool(ev.fflags & select.KQ_NOTE_FORK),
                                    "exit": bool(ev.fflags & select.KQ_NOTE_EXIT)}) + "\n")
        return got


class _ForkCutCase(_HelperCase):
    """The reviewer's cut, verbatim: registration gate + first discovery delayed until the
    root's positive exit (an acknowledged ordering, not a timer)."""

    def _cut_patches(self, run_id: str):
        gate = self.room.path / f"registered-{run_id}"
        calls = self.room.path / f"calls-{run_id}.jsonl"
        events = self.room.path / f"events-{run_id}.jsonl"
        original = pty_supervisor._Membership.discover

        def delayed(membership, reason):
            if reason == "watch_start":
                root = next(k[0] for k, v in membership.members.items() if v["role"] == pty_supervisor.MEMBER_ROLE_AGENT)
                gate.write_text("member registration done")
                deadline = time.monotonic() + 10
                while pty_supervisor._pid_presence(root) != "absent" and time.monotonic() < deadline:
                    time.sleep(.005)
                with calls.open("a") as f:
                    f.write(json.dumps({"root": root, "root_presence": pty_supervisor._pid_presence(root),
                                        "helper_info_present": self.info.exists()}) + "\n")
            return original(membership, reason)
        patches = [patch.object(pty_supervisor._Membership, "discover", delayed)]
        if sys.platform == "darwin":
            patches.append(patch.object(select, "kqueue", lambda: _RecordingKqueue(events)))
        return gate, calls, events, patches

    def _run_cut(self, run_id: str):
        gate, calls, events, patches = self._cut_patches(run_id)
        agent = self.room.path / f"gated-root-{run_id}.py"
        agent.write_text(_GATED_ROOT % (str(gate), str(self.info)))
        for p in patches:
            p.__enter__()
        try:
            session, sentinel = spawn_session(self.room, "", run_id=run_id, argv=[PYTHON, str(agent)], image=PYTHON,
                                              binding_mode="session_field", binding_field="session_id")
            result = session.await_completion()
        finally:
            for p in reversed(patches):
                p.__exit__(None, None, None)
        self.helper = self._helper_pid() or 0
        self.helper_start = pty_supervisor.proc_start_ticks(self.helper) if self.helper else 0
        self.assertGreater(self.helper_start, 0, "the detached helper is not alive")
        self.assertEqual(result["state"], "COMPLETED", result)
        recorded = [json.loads(x) for x in calls.read_text().splitlines()]
        self.assertTrue(recorded and recorded[0]["root_presence"] == "absent" and recorded[0]["helper_info_present"],
                        f"the cut was not taken as the reviewer specified: {recorded}")
        return session, sentinel, events


# =====================================================================================
# F-010 -- the registered-fork / parent-exit cut
# =====================================================================================
class F010RegisteredForkParentExitTests(_ForkCutCase):
    @DARWIN_ONLY
    def test_the_reviewers_cut_is_descendants_unknown_with_the_fork_named(self) -> None:
        session, _sentinel, events = self._run_cut("f010-cut")
        kernel = [json.loads(x) for x in events.read_text().splitlines()]
        root = session.pty["pid"]
        self.assertTrue(any(e["ident"] == root and e["fork"] for e in kernel), kernel)
        residual = session.membership_residual()
        entry = self._assert_unknown(session, residual, counter="forks_coalesced",
                                     reason_prefix=f"fork_coalesced:parent:{root}", pids=(root,))
        self.assertEqual(entry["listing_unreadable"], 0)
        fork = entry["forks"][0]
        self.assertEqual(fork["parent"]["pid"], root)
        self.assertEqual(fork["parent"]["start_id"], session.record["proc_start_ticks"])
        self.assertTrue(fork["evidence"]["note_fork"])
        self.assertTrue(fork["evidence"]["parent_exited"], fork)
        records = [r for r in self._ledger_discovery_records(session)
                   if r["kind"] == pty_supervisor.DISCOVERY_FORK_COALESCED]
        self.assertEqual(len(records), 1, records)
        self.assertEqual(records[0]["parent"]["incarnation"], session.fence)
        self.assertEqual(records[0]["evidence"]["fflags"] & select.KQ_NOTE_FORK, select.KQ_NOTE_FORK)

    @DARWIN_ONLY
    def test_the_cut_is_recovered_by_an_adopting_successor(self) -> None:
        session, sentinel, _events = self._run_cut("f010-adopt")
        s = successor(self.room, self._info_for(session, sentinel))
        self.assertEqual(settle(s)["state"], "COMPLETED")
        residual = s.membership_residual()
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)
        entry = [u for u in residual["unknown"] if u.get("discovery")][0]
        self.assertGreaterEqual(entry["forks_coalesced"], 1)
        s._reclaim(reason="lock-f010-adopted")
        rows = [r for r in s.journal.rows_for(s.intent_id) if r.get("event") == "descendants_unreaped"]
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[-1]["source_vocabulary"]["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)
        self.assertEqual(pty_supervisor.proc_start_ticks(self.helper), self.helper_start)

    @LINUX_ONLY
    def test_the_same_cut_on_linux_attributes_the_helper_through_the_subreaper(self) -> None:
        """Linux has no fork events; the orphaned helper reparents to the watcher (a subreaper)
        and is attributed POSITIVELY by the delayed walk: the residual names it alive."""
        session, _sentinel, _events = self._run_cut("f010-linux")
        residual = session.membership_residual()
        alive = [m for m in residual["alive"] if m["pid"] == self.helper]
        self.assertEqual(len(alive), 1, residual)
        self.assertTrue(alive[0]["observed_via"].startswith("subreaper_reparent:"), alive[0])
        self.assertIsNone(residual["outcome"], residual)
        session._reclaim(reason="lock-f010-linux")
        rows = [r for r in session.journal.rows_for(session.intent_id) if r.get("event") == "descendants_unreaped"]
        self.assertEqual([m["pid"] for m in rows[0]["source_vocabulary"]["alive"]], [self.helper])
        self.assertEqual(pty_supervisor.proc_start_ticks(self.helper), self.helper_start)

    def test_the_acknowledged_control_lists_the_helper_and_keeps_the_fork_unknown(self) -> None:
        """With the root gated until the ledger names the helper (an acknowledged ordering),
        the child is POSITIVELY attributed -- and the fork event stays a named unknown."""
        session, _sentinel = self._run("f010-control", ack="attributed")
        residual = session.membership_residual()
        # superseded by OS-48 i7 (conservative model): a coalesced NOTE_FORK is never
        # discharged by an attributed child -- the helper is listed alive AND the residual is
        # `descendants_unknown` (`fork_coalesced` + the helper's `fork_watch_gap`).
        self.assertIn(self.helper, [m["pid"] for m in residual["alive"]])
        state = self._state(session)["discovery"]
        if sys.platform == "darwin":
            self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)
            self.assertGreaterEqual(state["forks_coalesced"], 1, state)
            self.assertGreaterEqual(state["watch_gaps"], 1, state)
        else:                                          # no fork events: the subreaper attributes positively
            self.assertIsNone(residual["outcome"], residual)
        self.assertEqual((state["parents_unreadable"], state["candidates_unreadable"]), (0, 0), state)


# =====================================================================================
# F-010 adversarial pass -- every other silent-drop path is a named record
# =====================================================================================
class F010OtherSilentDropPathsTests(_HelperCase):
    @DARWIN_ONLY
    def test_a_watch_that_cannot_be_registered_is_unobservable(self) -> None:
        """The helper is attributed but its own fork/exit watch fails (ESRCH injected at the
        EV_ADD for that pid): its forks are unobservable -> `fork_watch_unregistered` naming it."""
        real_kqueue = select.kqueue
        me = self

        class Refusing:
            def __init__(self):
                self.real = real_kqueue()

            def fileno(self):
                return self.real.fileno()

            def close(self):
                return self.real.close()

            def control(self, changes, *a, **kw):
                for ev in changes or ():
                    # the MEMBERSHIP watch (NOTE_FORK|NOTE_EXIT) for the helper is refused;
                    # the presence probe (a one-shot NOTE_EXIT) is left truthful (i8)
                    if ((ev.flags & select.KQ_EV_ADD) and (ev.fflags & select.KQ_NOTE_FORK)
                            and int(ev.ident) == (me._helper_pid() or -1)):
                        raise OSError(errno.ESRCH, "lock: watch refused for the helper")
                return self.real.control(changes, *a, **kw)
        session, _sentinel = self._run("f010-unreg", patches=(patch.object(select, "kqueue", Refusing),), ack="attributed")
        residual = session.membership_residual()
        self.assertIn(self.helper, [m["pid"] for m in residual["alive"]], residual)     # the positive set is KEPT
        self._assert_unknown_keeping_alive(session, residual, counter="unobservable",
                                           reason_prefix="fork_watch_unregistered:ProcessLookupError:3", pids=(self.helper,))

    @DARWIN_ONLY
    def test_no_kqueue_is_fork_watch_unavailable(self) -> None:
        session, _sentinel = self._run("f010-nokq", patches=(
            patch.object(select, "kqueue", side_effect=OSError(errno.EMFILE, "lock: no kqueue")),))
        residual = session.membership_residual()
        self._assert_unknown_keeping_alive(session, residual, counter="unobservable",
                                           reason_prefix="fork_watch_unavailable:kqueue_unavailable")

    @DARWIN_ONLY
    def test_an_unreadable_event_read_is_named_once(self) -> None:
        real_kqueue = select.kqueue

        class Failing:
            def __init__(self):
                self.real = real_kqueue()
                self.failed = 0

            def fileno(self):
                return self.real.fileno()

            def close(self):
                return self.real.close()

            def control(self, changes, max_events=0, timeout=None):
                if changes is None and self.failed < 2:
                    self.failed += 1
                    raise OSError(errno.EINTR, "lock: kevent read failed")
                return self.real.control(changes, max_events, timeout)
        session, _sentinel = self._run("f010-events", patches=(patch.object(select, "kqueue", Failing),))
        residual = session.membership_residual()
        entry = self._assert_unknown_keeping_alive(session, residual, counter="unobservable",
                                                   reason_prefix="fork_events_unreadable:InterruptedError:4")
        self.assertEqual([r for r in entry["reasons"] if r.startswith("fork_events_unreadable")],
                         ["fork_events_unreadable:InterruptedError:4"])
        self.assertEqual(len([r for r in self._ledger_discovery_records(session)
                              if r["kind"] == pty_supervisor.DISCOVERY_EVENTS_UNREADABLE]), 1)

    def test_a_descendant_the_ceiling_would_drop_is_named(self) -> None:
        """`_MEMBER_CEILING = 1`: the root fills the ledger, the positively attributed helper
        cannot be recorded -> `member_ceiling_exceeded` naming it, never silently absent."""
        def ceiling_named(session) -> bool:          # acknowledged gate: the root lives until the
            return any(r["kind"] == pty_supervisor.DISCOVERY_CEILING_EXCEEDED     # watcher RECORDED the refusal
                       and (self._helper_pid() or -1) in r["pids"]
                       for r in self._ledger_discovery_records(session))
        session, _sentinel = self._run("f010-ceiling", patches=(patch.object(pty_supervisor, "_MEMBER_CEILING", 1),),
                                       ack=ceiling_named)
        residual = session.membership_residual()
        self._assert_unknown_keeping_alive(session, residual, counter="unobservable",
                                           reason_prefix="member_ceiling_exceeded:ceiling:1", pids=(self.helper,))
        self.assertEqual(residual["members"], 1)

    def test_a_parent_unreadable_by_every_source_is_named(self) -> None:
        """The helper is readable and names the root as its parent, but the root's identity
        can be re-read by no source while the kernel still holds it: the attribution can be
        neither made nor refused -> `parent_identity_unreadable` naming the helper."""
        real_info = pty_supervisor._process_info
        real_fallback = pty_supervisor._process_info_fallback
        root_pid = {"pid": None}

        def _is_root(pid):
            return root_pid["pid"] is not None and pid == root_pid["pid"]

        def denied(pid):
            return None if _is_root(pid) else real_info(pid)

        def denied_fallback(pid):
            return None if _is_root(pid) else real_fallback(pid)
        original_discover = pty_supervisor._Membership.discover

        def note_root(membership, reason):
            if root_pid["pid"] is None:
                root_pid["pid"] = next(k[0] for k, v in membership.members.items() if v["role"] == pty_supervisor.MEMBER_ROLE_AGENT)
            return original_discover(membership, reason)
        patches = [patch.object(pty_supervisor, "_process_info", side_effect=denied),
                   patch.object(pty_supervisor, "_process_info_fallback", side_effect=denied_fallback),
                   patch.object(pty_supervisor._Membership, "discover", note_root)]
        if sys.platform == "linux":
            # i8 (F-016): on Linux the held pidfd is a source too -- "unreadable by every
            # source" means the root's fixed object answers nothing either
            real_state = pty_supervisor._Membership._pidfd_state

            def denied_state(membership, key):
                return "unavailable" if _is_root(key[0]) else real_state(membership, key)
            patches.append(patch.object(pty_supervisor._Membership, "_pidfd_state", denied_state))
        patches = tuple(patches)

        def parent_named(session) -> bool:           # the acknowledged ordering gate: the root
            return any(r["kind"] == pty_supervisor.DISCOVERY_PARENT_UNREADABLE   # stays alive until the
                       and (self._helper_pid() or -1) in r["pids"]              # watcher has RECORDED it
                       for r in self._ledger_discovery_records(session))
        session, _sentinel = self._run("f010-parent", patches=patches, ack=parent_named)
        residual = session.membership_residual()
        self._assert_unknown_keeping_alive(session, residual, counter="parents_unreadable",
                                           reason_prefix="parent_identity_unreadable:parent_member_unreadable",
                                           pids=(self.helper,))

    def _assert_unknown_keeping_alive(self, session, residual, *, counter, reason_prefix, pids=()):
        """Like `_assert_unknown` but the positive set may be non-empty (the drop path is
        about OTHER descendants); the durable record, the named row and non-signalling hold."""
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)
        entries = [u for u in residual["unknown"] if u.get("discovery") == "unreadable"]
        self.assertEqual(len(entries), 1, residual["unknown"])
        entry = entries[0]
        self.assertGreaterEqual(int(entry[counter] or 0), 1, entry)
        self.assertTrue(any(r.startswith(reason_prefix) for r in entry["reasons"]), entry["reasons"])
        for pid in pids:
            self.assertIn(pid, entry["pids"], entry)
        state = self._state(session)
        self.assertGreaterEqual(int(state["discovery"][counter]), 1, state)
        self.assertGreaterEqual(len(self._ledger_discovery_records(session)), 1)
        self.assertEqual(session._boundary["fence"]["fence"], session.fence)
        session._reclaim(reason="lock-f010")
        rows = [r for r in session.journal.rows_for(session.intent_id) if r.get("event") == "descendants_unreaped"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_vocabulary"]["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)
        self.assertEqual(pty_supervisor.proc_start_ticks(self.helper), self.helper_start, "a member / unknown was signalled")
        return entry


# =====================================================================================
# F-011 -- the historical-artifact lock's scope, on the real tree
# =====================================================================================
class F011HistoricalScopeTests(unittest.TestCase):
    def test_the_real_tree_excludes_only_positively_active_runs(self) -> None:
        """superseded by OS-48 i8 (F-011): the active set is what the invocation names or a
        positive live-writer fact; the expected set is computed by the ROOT run-directory
        component only (never by intersecting every path component)."""
        from scripts.test_os42_artifacts import REPO_ROOT, HistoricalArtifactTests as T
        runs = REPO_ROOT / "artifacts" / "runs"
        if not runs.is_dir():
            self.skipTest("no run artifacts in this checkout")
        active = T.active_runs(runs)
        digest = T.historical_digest(runs)
        settled_on_disk = {str(p.relative_to(runs)) for p in runs.rglob("*")
                           if p.is_file() and p.relative_to(runs).parts[0] not in active}
        self.assertEqual(settled_on_disk, set(digest))
        self.assertTrue(settled_on_disk, "nothing to protect")


if __name__ == "__main__":
    unittest.main()
