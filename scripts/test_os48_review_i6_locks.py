"""OS-48 locks for REVIEW_IMPLEMENTATION_iteration6.md (F-010 / F-011 / F-012 / F-013) under the
CONSERVATIVE descendant model of iteration 7: the residual is UNKNOWN by default, and only an
enumerated set of positive facts (P1-P5 in `_Membership`) contributes to the answer.  Every
lock runs the reviewer's construction through the REAL watcher / runtime (or the real lock).

* F-010 darwin: a coalesced NOTE_FORK is never discharged -- with A attributed, B->G reparented
  and G alive, the residual is `descendants_unknown` (`fork_coalesced`, parent identity +
  event), A is listed alive, G is unsignalled; the pre-exec fork watch (`spawn`'s gate) sees a
  fork that happened BEFORE the watcher's first walk; a root whose watch could not be
  registered is a named `fork_watch_gap`; every descendant carries its own `fork_watch_gap`;
  the only clean darwin dispatch is a root with a registered watch that never forked; the
  adopting successor reads the same residual.
* F-012 both platforms: a held zombie pid with unreadable start identity is `unreadable`, so a
  stale cached incarnation never attributes a real child (`parent_identity_unreadable`); a
  readable current mismatch is refused positively; a live parent with a matching current
  start is the only attribution.
* F-013 Linux: a subreaper-parented orphan whose start identity is unreadable (zero) is
  `candidate_identity_unreadable` -- named, never omitted -- on the supervisor and adoption
  paths; the readable control attributes it positively.
* F-011: the historical-artifact lock's active set is what the invocation names (env / this
  session's own binding); a terminal run with a stale unreleased binding and a nested
  active-name path are protected on the real tree.
"""
from __future__ import annotations

import contextlib
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
from scripts.test_os48_review_i4_locks import _HelperCase  # noqa: E402

DARWIN_ONLY = unittest.skipUnless(sys.platform == "darwin", "OS-48 libproc evidence seams are darwin's (probe_03)")
LINUX_ONLY = unittest.skipUnless(sys.platform == "linux", "the /proc discovery walk is Linux's")

#: The reviewer's coalesced-fork root: gated until the test says the watcher's first walk is
#: done (`gate1`), forks A (a surviving direct child: own session, SIGHUP ignored) and B; B forks G (detached, SIGHUP
#: ignored) and exits; the root reaps B, records {A, G}, waits for the test's acknowledgement
#: of A's attribution (`gate2`), then emits its bound completion and exits.
_COALESCED_ROOT = """import os,time,json,signal
deadline=time.monotonic()+15
while not os.path.exists(%(gate1)r) and time.monotonic()<deadline: time.sleep(.005)
a=os.fork()
if a==0:
    os.setsid();signal.signal(signal.SIGHUP,signal.SIG_IGN)
    time.sleep(45);os._exit(0)
r,w=os.pipe();b=os.fork()
if b==0:
    os.close(r);g=os.fork()
    if g==0:
        os.setsid();signal.signal(signal.SIGHUP,signal.SIG_IGN)
        os.write(w,str(os.getpid()).encode());os.close(w);time.sleep(45);os._exit(0)
    os.close(w);os._exit(0)
os.close(w);g=int(os.read(r,32));os.close(r);os.waitpid(b,0)
open(%(info)r,"w").write(json.dumps(dict(pid=g,a=a,g=g,b=b)))
deadline=time.monotonic()+15
while not os.path.exists(%(gate2)r) and time.monotonic()<deadline: time.sleep(.005)
os.write(1,(json.dumps(dict(type="result",is_error=False,session_id=os.environ["OS48_TEST_SID"]))+"\\n").encode());os._exit(0)
"""

#: The reviewer's early-fork root: forks B at once; B forks G (detached) and exits; the root
#: reaps B, records {G}, then waits for the test's gate before completing.
_EARLY_ROOT = """import os,time,json,signal
r,w=os.pipe();b=os.fork()
if b==0:
    os.close(r);g=os.fork()
    if g==0:
        os.setsid();signal.signal(signal.SIGHUP,signal.SIG_IGN)
        os.write(w,str(os.getpid()).encode());os.close(w);time.sleep(45);os._exit(0)
    os.close(w);os._exit(0)
os.close(w);g=int(os.read(r,32));os.close(r);os.waitpid(b,0)
open(%(info)r,"w").write(json.dumps(dict(pid=g,g=g,b=b,root=os.getpid())))
deadline=time.monotonic()+15
while not os.path.exists(%(gate2)r) and time.monotonic()<deadline: time.sleep(.005)
os.write(1,(json.dumps(dict(type="result",is_error=False,session_id=os.environ["OS48_TEST_SID"]))+"\\n").encode());os._exit(0)
"""

_NO_FORK_ROOT = """import os,json
os.write(1,(json.dumps(dict(type="result",is_error=False,session_id=os.environ["OS48_TEST_SID"]))+"\\n").encode());os._exit(0)
"""


def _quiet(*calls) -> None:
    """Run each zero-arg callable, ignoring OSError (cleanup that must not mask a verdict)."""
    for call in calls:
        try:
            call()
        except OSError:
            pass


def _kill_pinned(pid: int, start: int) -> None:
    if pid and start and pty_supervisor.proc_start_ticks(pid) == start:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


class _ForkTreeCase(_HelperCase):
    """Runs a root script with the test opening its gates on ACKNOWLEDGED ledger facts."""

    def _run_script(self, run_id: str, script: str, *, gate1=None, gate2=None, patches=()):
        info = self.info
        g1 = self.room.path / f"gate1-{run_id}"
        g2 = self.room.path / f"gate2-{run_id}"
        agent = self.room.path / f"root-{run_id}.py"
        agent.write_text(script % {"gate1": str(g1), "gate2": str(g2), "info": str(info)})
        for p in patches:
            p.__enter__()
        try:
            session, sentinel = spawn_session(self.room, "", run_id=run_id, argv=[PYTHON, str(agent)], image=PYTHON,
                                              binding_mode="session_field", binding_field="session_id",
                                              pump_until_sentinel=False)
            deadline = time.time() + 30
            opened = {1: gate1 is None, 2: gate2 is None}
            if opened[1]:
                g1.write_text("go")
            if opened[2]:
                g2.write_text("go")
            while not sentinel.exists() and time.time() < deadline:
                session.pump(timeout_ms=20)
                tree = self._tree_if_written()
                if not opened[1] and gate1(session, tree):
                    g1.write_text("go")
                    opened[1] = True
                if opened[1] and not opened[2] and gate2(session, tree):
                    g2.write_text("go")
                    opened[2] = True
            self.assertTrue(sentinel.exists(), "the watcher never wrote the sentinel")
            self.assertTrue(opened[1] and opened[2], f"an ordering gate was never satisfied: {opened}")
            session.pump(timeout_ms=50)
            result = session.await_completion()
        finally:
            for p in reversed(patches):
                p.__exit__(None, None, None)
        self.assertEqual(result["state"], "COMPLETED", result)
        tree = self._tree_if_written() or {}
        pinned = {name: (int(pid), pty_supervisor.proc_start_ticks(int(pid))) for name, pid in tree.items() if name in ("a", "g")}
        for name, (pid, start) in pinned.items():
            self.addCleanup(_kill_pinned, pid, start)
            self.assertGreater(start, 0, f"{name} ({pid}) is not alive")
        return session, sentinel, tree, pinned

    def _tree_if_written(self):
        try:
            return json.loads(self.info.read_text()) if self.info.exists() else None
        except (OSError, ValueError):
            return None

    def _state_passes(self, session) -> int:
        try:
            return int(self._state(session)["discovery"]["passes"])
        except (OSError, ValueError, KeyError):
            return 0

    def _attributed(self, session, pid: int) -> bool:
        ledger = pty_supervisor.read_ledger(session._members_path())
        return any(r.get("event") == pty_supervisor.MEMBER_EVENT_OBSERVED
                   and int((r.get("identity") or {}).get("pid") or 0) == pid for r in ledger["records"])

    def _fork_records(self, session):
        return [r for r in self._ledger_discovery_records(session) if r["kind"] == pty_supervisor.DISCOVERY_FORK_COALESCED]


# =====================================================================================
# F-010 -- the coalesced fork and the pre-registration window
# =====================================================================================
class F010CoalescedForkTests(_ForkTreeCase):
    @DARWIN_ONLY
    def test_a_coalesced_fork_stays_unknown_after_one_child_is_attributed(self) -> None:
        """`probe_coalesced_fork_real`: A attributed (listed alive), B->G reparented before the
        walk; the NOTE_FORK is a `fork_coalesced` unknown that A's attribution does NOT
        discharge; G stays alive and unsignalled; the named row and the fence hold."""
        session, _sentinel, tree, pinned = self._run_script(
            "f010-coalesced", _COALESCED_ROOT,
            gate1=lambda s, t: self._state_passes(s) >= 1,
            gate2=lambda s, t: bool(t) and self._attributed(s, int(t["a"])))
        residual = session.membership_residual()
        root = session.pty["pid"]
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)
        self.assertIn(pinned["a"][0], [m["pid"] for m in residual["alive"]], residual)
        self.assertNotIn(pinned["g"][0], [m["pid"] for m in residual["alive"]])       # G is UNKNOWN, not absent
        entry = [u for u in residual["unknown"] if u.get("discovery") == "unreadable"][0]
        self.assertGreaterEqual(entry["forks_coalesced"], 1, entry)
        self.assertIn(f"fork_coalesced:parent:{root}", entry["reasons"])
        record = [r for r in self._fork_records(session) if r["pids"] == [root]][0]
        self.assertEqual((record["parent"]["pid"], record["parent"]["start_id"]), (root, session.record["proc_start_ticks"]))
        self.assertTrue(record["evidence"]["note_fork"])
        self.assertEqual(session._boundary["fence"]["fence"], session.fence)
        session._reclaim(reason="lock-f010")
        rows = [r for r in session.journal.rows_for(session.intent_id) if r.get("event") == "descendants_unreaped"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_vocabulary"]["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)
        for name, (pid, start) in pinned.items():
            self.assertEqual(pty_supervisor.proc_start_ticks(pid), start, f"{name} was signalled")

    @DARWIN_ONLY
    def test_a_fork_before_the_first_walk_is_seen_by_the_pre_exec_watch(self) -> None:
        """`probe_early_fork_before_watch_real`: the initial `_add` is delayed until the root
        has forked B, B forked G and the root reaped B; the truthful first walk then finds
        nothing.  The root's watch was registered BEFORE its exec by `spawn`'s gate, so the
        fork is still observed: `fork_coalesced`, `root_watch=registered`, G unknown."""
        add = pty_supervisor._Membership.discover
        real_add = pty_supervisor._Membership._add
        info = self.info

        def delayed_add(m, pid, st, role, via):
            if role == pty_supervisor.MEMBER_ROLE_AGENT:
                deadline = time.monotonic() + 15
                while not info.exists() and time.monotonic() < deadline:
                    time.sleep(.005)
            return real_add(m, pid, st, role, via)
        session, _sentinel, tree, pinned = self._run_script(
            "f010-early", _EARLY_ROOT, gate2=lambda s, t: self._state_passes(s) >= 1,
            patches=(patch.object(pty_supervisor._Membership, "_add", delayed_add),))
        residual = session.membership_residual()
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)
        entry = [u for u in residual["unknown"] if u.get("discovery") == "unreadable"][0]
        self.assertEqual(entry["root_watch"], "registered")
        self.assertIn(f"fork_coalesced:parent:{session.pty['pid']}", entry["reasons"])
        self.assertEqual(residual["alive"], [])
        session._reclaim(reason="lock-f010")
        self.assertEqual(pty_supervisor.proc_start_ticks(pinned["g"][0]), pinned["g"][1])

    @DARWIN_ONLY
    def test_a_root_watch_that_could_not_be_registered_is_a_named_gap(self) -> None:
        real = pty_supervisor._register_root_watch

        def failing(pid):
            kq, _status = real(pid)
            return kq, "ProcessLookupError:3"
        session, _sentinel, _tree, _pinned = self._run_script(
            "f010-rootgap", _NO_FORK_ROOT, patches=(patch.object(pty_supervisor, "_register_root_watch", failing),))
        residual = session.membership_residual()
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)
        entry = [u for u in residual["unknown"] if u.get("discovery") == "unreadable"][0]
        self.assertEqual(entry["root_watch"], "ProcessLookupError:3")
        self.assertIn("fork_watch_gap:root:ProcessLookupError:3", entry["reasons"])
        self.assertGreaterEqual(entry["watch_gaps"], 1)

    def test_a_root_with_a_registered_watch_that_never_forked_is_clean(self) -> None:
        session, _sentinel, _tree, _pinned = self._run_script("f010-clean", _NO_FORK_ROOT)
        residual = session.membership_residual()
        self.assertIsNone(residual["outcome"], residual)
        self.assertEqual(residual["discovery"], "readable")
        state = self._state(session)["discovery"]
        self.assertEqual(state["root_watch"], "registered" if sys.platform == "darwin" else "subreaper")
        self.assertEqual((state["forks_coalesced"], state["watch_gaps"], state["candidates_unreadable"],
                          state["parents_unreadable"], state["unobservable"]), (0, 0, 0, 0, 0), state)
        self.assertEqual(self._ledger_discovery_records(session), [])

    def test_a_root_whose_start_is_unreadable_is_named(self) -> None:
        """`_Membership` built with a zero root start (the typed unreadable sentinel): the
        root is named `candidate_identity_unreadable:root_start_unreadable`, never a silent
        empty set."""
        m = pty_supervisor._Membership(os.fsencode(self.room.path / "root0.jsonl"), fence="model:i",
                                       boot_id=pty_supervisor.host_boot_id(), agent_pid=os.getpid(), agent_start_id=0,
                                       root_watch="registered")
        self.addCleanup(m.close)
        self.assertEqual(m.members, {})
        self.assertEqual(m.discovery["candidates_unreadable"], 1, m.discovery)
        self.assertIn("candidate_identity_unreadable:root_start_unreadable", m.discovery["reasons"])
        self.assertIn(os.getpid(), m.discovery["pids"])

    @DARWIN_ONLY
    def test_every_attributed_descendant_carries_its_own_watch_gap(self) -> None:
        session, _sentinel = self._run("f010-gap", ack="attributed")
        entry = [u for u in session.membership_residual()["unknown"] if u.get("discovery") == "unreadable"][0]
        gaps = [r for r in self._ledger_discovery_records(session) if r["kind"] == pty_supervisor.DISCOVERY_FORK_WATCH_GAP]
        self.assertEqual([r["pids"] for r in gaps], [[self.helper]], gaps)
        self.assertEqual(gaps[0]["reason"], "registered_after_birth")
        self.assertGreaterEqual(entry["watch_gaps"], 1)

    @DARWIN_ONLY
    def test_the_coalesced_residual_is_recovered_by_an_adopting_successor(self) -> None:
        session, sentinel, _tree, pinned = self._run_script(
            "f010-adopt", _COALESCED_ROOT, gate1=lambda s, t: self._state_passes(s) >= 1,
            gate2=lambda s, t: bool(t) and self._attributed(s, int(t["a"])))
        s = successor(self.room, self._info_for(session, sentinel))
        self.assertEqual(settle(s)["state"], "COMPLETED")
        residual = s.membership_residual()
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)
        self.assertIn(pinned["a"][0], [m["pid"] for m in residual["alive"]])
        self.assertIn(f"fork_coalesced:parent:{session.pty['pid']}",
                      [u for u in residual["unknown"] if u.get("discovery") == "unreadable"][0]["reasons"])
        s._reclaim(reason="lock-f010-adopted")
        rows = [r for r in s.journal.rows_for(s.intent_id) if r.get("event") == "descendants_unreaped"]
        self.assertGreaterEqual(len(rows), 1)



# =====================================================================================
# F-012 -- a held zombie pid is not a witness of the cached incarnation
# =====================================================================================
class F012HeldZombieTests(unittest.TestCase):
    """The reviewer's caller-level construction: a stale cached `(pid, prior_start)` member
    whose numeric pid is held by an UNRELATED current process that forks a real child; the
    child's truthful info receipt precedes the parent's ordinary exit; the parent's two
    identity reads are refused; the parent is then a real held zombie."""

    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)

    def _tree(self):
        root_r, root_w = os.pipe()
        dispatch_root = os.fork()
        if dispatch_root == 0:
            os.close(root_w)
            os.read(root_r, 1)
            os._exit(0)
        os.close(root_r)
        self.addCleanup(_quiet, lambda: os.write(root_w, b"r"), lambda: os.close(root_w), lambda: os.waitpid(dispatch_root, 0))
        ready_r, ready_w = os.pipe()
        gate_r, gate_w = os.pipe()
        hold_r, hold_w = os.pipe()
        parent = os.fork()
        if parent == 0:
            os.close(ready_r); os.close(gate_w); os.close(hold_w)
            helper = os.fork()
            if helper == 0:
                os.close(ready_w); os.close(gate_r)
                os.read(hold_r, 1)
                os._exit(0)
            os.close(hold_r)
            os.write(ready_w, str(helper).encode()); os.close(ready_w)
            os.read(gate_r, 1)
            os._exit(0)
        os.close(ready_w); os.close(gate_r); os.close(hold_r)
        helper = int(os.read(ready_r, 100)); os.close(ready_r)
        self.addCleanup(_quiet, lambda: os.close(hold_w))
        self.addCleanup(_quiet, lambda: os.waitpid(parent, 0))
        return dispatch_root, parent, helper, gate_w

    def test_a_held_zombie_with_denied_start_reads_is_unreadable_and_attributes_nothing(self) -> None:
        dispatch_root, parent, helper, gate_w = self._tree()
        root_start = pty_supervisor.proc_start_ticks(dispatch_root)
        current = pty_supervisor._process_info(parent) or pty_supervisor._process_info_fallback(parent)
        self.assertTrue(current)
        childinfo = pty_supervisor._process_info(helper) or pty_supervisor._process_info_fallback(helper)
        self.assertEqual(childinfo[0], parent)
        prior = pty_supervisor.proc_start_ticks(os.getpid())            # a start id the pid never had
        self.assertNotEqual(prior, current[1])
        m = pty_supervisor._Membership(os.fsencode(self.room.path / "members.jsonl"), fence="model:i",
                                       boot_id=pty_supervisor.host_boot_id(), agent_pid=dispatch_root,
                                       agent_start_id=root_start)
        self.addCleanup(m.close)
        record = {"schema": pty_supervisor.MEMBER_SCHEMA, "event": pty_supervisor.MEMBER_EVENT_OBSERVED,
                  "identity": pty_supervisor._member_identity(parent, prior, pty_supervisor.host_boot_id(), "model:i"),
                  "role": pty_supervisor.MEMBER_ROLE_DESCENDANT, "observed_via": "fixture_prior_incarnation", "pgid": 0}
        m.members[(parent, prior, 1)] = record                # a cached lifetime with NO fixed object
        m._append(record)
        self.addCleanup(_quiet, lambda: os.close(gate_w))
        if sys.platform == "darwin":
            self.assertIsNone(m._live_member_for(parent), "a readable CURRENT mismatch must be refused positively")
        else:
            # F-016 (i8): on Linux identity is asked of the FIXED object only; a cached member
            # without a pidfd is unreadable -- never a witness, never a positive refusal
            self.assertEqual(m._live_member_for(parent), "unreadable")
        real_info, real_fallback = pty_supervisor._process_info, pty_supervisor._process_info_fallback
        done = {"ordered": False}

        def info(pid):
            if pid == parent:
                return None                                            # both sources refused
            if pid == helper and not done["ordered"]:
                got = real_info(pid) or real_fallback(pid)
                os.close(gate_w)
                done["ordered"] = True
                limit = time.monotonic() + 10
                while pty_supervisor._pid_presence(parent) != "absent" and time.monotonic() < limit:
                    time.sleep(.005)
                os.kill(parent, 0)                                     # a real held zombie
                return got
            return real_info(pid)

        def fallback(pid):
            return None if pid == parent else real_fallback(pid)
        with patch.object(pty_supervisor, "_process_info", side_effect=info), \
                patch.object(pty_supervisor, "_process_info_fallback", side_effect=fallback), \
                patch.object(m, "_list_candidates", return_value=([helper], "")):
            added = m.discover("pid_reuse_and_parent_read_denied")
            eligible = m._live_member_for(parent)
        self.assertEqual(added, 0)
        self.assertEqual(eligible, "unreadable")
        self.assertNotIn(helper, [k[0] for k in m.members], "the real child was attributed to a stale cached incarnation")
        self.assertEqual(m.discovery["parents_unreadable"], 1, m.discovery)
        self.assertIn("parent_identity_unreadable:parent_member_unreadable", m.discovery["reasons"])
        self.assertIn(helper, m.discovery["pids"])
        recs = [r for r in pty_supervisor.read_ledger(m.path)["records"]
                if r.get("event") == pty_supervisor.MEMBER_EVENT_DISCOVERY_UNREADABLE
                and r["kind"] == pty_supervisor.DISCOVERY_PARENT_UNREADABLE]
        self.assertEqual([r["pids"] for r in recs], [[helper]])

    def test_a_live_parent_with_a_matching_current_start_is_the_only_attribution(self) -> None:
        """The legitimate bound-child control: the parent's CURRENT start equals the cached
        one and it is alive -> the child is a positive member."""
        dispatch_root, parent, helper, gate_w = self._tree()
        self.addCleanup(_quiet, lambda: os.close(gate_w))
        current = pty_supervisor._process_info(parent) or pty_supervisor._process_info_fallback(parent)
        m = pty_supervisor._Membership(os.fsencode(self.room.path / "members2.jsonl"), fence="model:i",
                                       boot_id=pty_supervisor.host_boot_id(), agent_pid=dispatch_root,
                                       agent_start_id=pty_supervisor.proc_start_ticks(dispatch_root))
        self.addCleanup(m.close)
        record = {"schema": pty_supervisor.MEMBER_SCHEMA, "event": pty_supervisor.MEMBER_EVENT_OBSERVED,
                  "identity": pty_supervisor._member_identity(parent, current[1], pty_supervisor.host_boot_id(), "model:i"),
                  "role": pty_supervisor.MEMBER_ROLE_DESCENDANT, "observed_via": "fixture_current_incarnation", "pgid": 0}
        m.members[(parent, current[1], 1)] = record
        m._append(record)
        if sys.platform == "linux":                      # F-016: the parent witness is the held pidfd
            m._pidfds[(parent, current[1], 1)] = os.pidfd_open(parent)
        self.assertIs(m._live_member_for(parent), record)
        with patch.object(m, "_list_candidates", return_value=([helper], "")):
            added = m.discover("bound_child_control")
        self.assertEqual(added, 1)
        self.assertEqual(m.discovery["parents_unreadable"], 0)
        self.assertIn(helper, [k[0] for k in m.members])


# =====================================================================================
# F-013 -- Linux: a subreaper-parented orphan with an unreadable start identity
# =====================================================================================
class F013SubreaperZeroStartTests(_HelperCase):
    def _zero_start_for_helper(self):
        real = pty_supervisor._process_info

        def seam(pid):
            got = real(pid)
            if got is not None and self._helper_pid() == pid:
                return got[0], 0                                        # real ppid, typed unreadable start
            return got
        return patch.object(pty_supervisor, "_process_info", side_effect=seam)

    def _named(self, session) -> bool:
        return any(r["kind"] == pty_supervisor.DISCOVERY_CANDIDATE_UNREADABLE and (self._helper_pid() or -1) in r["pids"]
                   for r in self._ledger_discovery_records(session))

    @LINUX_ONLY
    def test_a_zero_start_subreaper_orphan_is_named_never_omitted(self) -> None:
        """`probe_linux_orphan_start_unreadable_real`: the helper's real ppid is the watcher
        (subreaper) and its start reads as the typed zero -> `candidate_identity_unreadable`
        naming it, `descendants_unknown`, a named row, the helper unsignalled."""
        session, _sentinel = self._run("f013-zero", patches=(self._zero_start_for_helper(),), ack=self._named)
        residual = session.membership_residual()
        self._assert_unknown(session, residual, counter="candidates_unreadable",
                             reason_prefix="candidate_identity_unreadable:process_info_unreadable", pids=(self.helper,))

    @LINUX_ONLY
    def test_the_zero_start_orphan_residual_is_recovered_by_an_adopting_successor(self) -> None:
        session, sentinel = self._run("f013-adopt", patches=(self._zero_start_for_helper(),), ack=self._named)
        s = successor(self.room, self._info_for(session, sentinel))
        self.assertEqual(settle(s)["state"], "COMPLETED")
        residual = s.membership_residual()
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)
        self.assertIn(self.helper, [u for e in residual["unknown"] if e.get("discovery") for u in e["pids"]])

    @LINUX_ONLY
    def test_the_readable_subreaper_orphan_is_a_positive_member(self) -> None:
        session, _sentinel = self._run("f013-control", ack="attributed")
        residual = session.membership_residual()
        alive = [m for m in residual["alive"] if m["pid"] == self.helper]
        self.assertEqual(len(alive), 1, residual)
        self.assertTrue(alive[0]["observed_via"].startswith(("subreaper_reparent:", "proc_ppid_walk:")), alive[0])
        self.assertEqual(self._state(session)["discovery"]["candidates_unreadable"], 0)


# =====================================================================================
# F-011 -- the historical lock's scope on the real tree
# =====================================================================================
class F011HistoricalScopeTests(unittest.TestCase):
    def test_active_runs_come_only_from_the_invocation_or_a_live_writer_fact(self) -> None:
        """superseded by OS-48 i8 (F-011): no hard-coded run, no session cookie."""
        from scripts.test_os42_artifacts import REPO_ROOT, HistoricalArtifactTests as T
        runs = REPO_ROOT / "artifacts" / "runs"
        if not runs.is_dir():
            self.skipTest("no run artifacts in this checkout")
        unnamed = {"OS42_ACTIVE_RUN_IDS": "", "CLAUDE_CODE_SESSION_ID": ""}
        live = T.active_runs(runs, unnamed)                       # only positive live-writer facts
        self.assertNotIn("run_70401d3e9964", live)
        named = {"OS42_ACTIVE_RUN_IDS": "run_x,run_y", "CLAUDE_CODE_SESSION_ID": "some-session"}
        self.assertEqual(T.active_runs(runs, named), live | {"run_x", "run_y"})
        digest = T.historical_digest(runs, named)
        on_disk = {str(p.relative_to(runs)) for p in runs.rglob("*") if p.is_file()
                   and p.relative_to(runs).parts[0] not in (live | {"run_x", "run_y"})}
        self.assertEqual(on_disk, set(digest))


if __name__ == "__main__":
    unittest.main()
