"""OS-48 locks for REVIEW_IMPLEMENTATION_iteration7.md (F-011 / F-014 / F-015 / F-016), each
through the REAL caller.

* F-015 (capture finality): a completion- or refusal-SHAPED JSON object inside a line of the
  fenced range that is not itself a record -- a cooperative helper's `progress: ` prefix on
  the root's own refusal, a suffix, a record split across two lines -- never yields COMPLETED:
  a refusing object is a refusal under R1 (`refusal_in_boundary`, FAILED); any other makes R2
  unprovable (`record_framing_ambiguous`, LOST).  The clean control and a prose line whose
  braces hold no completion-shaped object still settle as before; the fenced bytes are exact.
* F-014 (Linux ownership): `PR_SET_CHILD_SUBREAPER` is installed and VERIFIED (`PR_GET` == 1)
  in the watcher BEFORE the gate releases the root; the reviewer's pre-setup fork/reap cut is
  therefore covered; a failed / unverifiable setup is the durable `ownership_setup_unverified`
  residual for the whole dispatch.
* F-016 (Linux identity): every positively attributed member holds a pidfd (a FIXED object)
  until observed exiting; parent attribution goes through the held pidfd, never (pid, tick)
  equality; a cached EXITED key never suppresses a live candidate with the same key (a new
  lifetime); a cached UNEXITED key never adopts a different process's child.
* F-011 (history lock): "active" is only an explicit invocation scope or a positive
  live-writer fact; no hard-coded run, no session cookie; root-directory rule everywhere.
"""
from __future__ import annotations

import contextlib
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
from scripts.test_os48_review_i4_locks import _HelperCase  # noqa: E402

DARWIN_ONLY = unittest.skipUnless(sys.platform == "darwin", "OS-48 libproc evidence seams are darwin's (probe_03)")
LINUX_ONLY = unittest.skipUnless(sys.platform == "linux", "the /proc discovery walk is Linux's")

#: A dispatch-bound root: emits `first` (a newline-delimited record), lets a cooperative child
#: write `helper` to INHERITED stdout (acknowledged over a pipe, no timer), reaps it, then
#: writes `second` verbatim and exits 0.  With helper="" and second a clean record this is the
#: ordinary success/refusal sequence.
_FRAMING_ROOT = """import os,json,sys
sid=os.environ["OS48_TEST_SID"]
first=%(first)r; helper=%(helper)r; second=%(second)r
def rec(is_error, extra=""):
    return json.dumps(dict(type="result", is_error=is_error, session_id=sid, result="early body")) if not extra else extra
if first:
    os.write(1, (first.replace("SID", sid) + "\\n").encode())
if helper:
    r, w = os.pipe(); h = os.fork()
    if h == 0:
        os.close(r); os.write(1, helper.encode()); os.write(w, b"written"); os.close(w); os._exit(0)
    os.close(w); assert os.read(r, 32) == b"written"; os.close(r); os.waitpid(h, 0)
if second:
    os.write(1, second.replace("SID", sid).encode())
os._exit(0)
"""

SUCCESS = '{"type": "result", "is_error": false, "session_id": "SID", "result": "early body"}'
REFUSAL = '{"type": "result", "is_error": true, "session_id": "SID"}'


def _quiet(*calls) -> None:
    """Run each zero-arg callable, ignoring OSError (cleanup that must not mask a verdict)."""
    for call in calls:
        try:
            call()
        except OSError:
            pass


class F015FramingAmbiguityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)

    def _dispatch(self, run_id: str, *, first: str, helper: str, second: str):
        agent = self.room.path / f"root-{run_id}.py"
        agent.write_text(_FRAMING_ROOT % {"first": first, "helper": helper, "second": second})
        session, _sentinel = spawn_session(self.room, "", run_id=run_id, argv=[PYTHON, str(agent)], image=PYTHON,
                                           binding_mode="session_field", binding_field="session_id")
        result = session.await_completion()
        n = session._boundary["fence"]["boundary"]["offset_n"]
        prefix = session.capture.raw()[:n]
        return session, result, prefix

    def test_a_helper_prefixed_refusal_is_a_refusal_in_boundary(self) -> None:
        """`probe_helper_prefix_refusal_real`: the root's own refusal shares its line with a
        helper's `progress: ` -> FAILED `refusal_in_boundary`, never COMPLETED."""
        session, result, prefix = self._dispatch("f015-prefix", first=SUCCESS, helper="progress: ", second=REFUSAL + "\n")
        self.assertEqual(result["state"], "FAILED", result)
        self.assertEqual(result["verdict"]["reason"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, result)
        self.assertIn(b'progress: {"type": "result", "is_error": true', prefix)          # exact fenced bytes
        self.assertEqual(result["evidence"]["refusal"]["source"], "framing_ambiguous_refusal")

    def test_a_suffixed_refusal_is_a_refusal_in_boundary(self) -> None:
        session, result, prefix = self._dispatch("f015-suffix", first=SUCCESS, helper="", second=REFUSAL + " trailing\n")
        self.assertEqual(result["state"], "FAILED", result)
        self.assertEqual(result["verdict"]["reason"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY)

    def test_a_refusal_split_across_two_lines_is_a_refusal_in_boundary(self) -> None:
        split = '{"type": "result",\n "is_error": true, "session_id": "SID"}\n'
        session, result, prefix = self._dispatch("f015-split", first=SUCCESS, helper="", second=split)
        self.assertEqual(result["state"], "FAILED", result)
        self.assertEqual(result["verdict"]["reason"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY)
        self.assertIn(b'{"type": "result",\r\n "is_error": true', prefix.replace(b"\r\r\n", b"\r\n"))

    def test_a_helper_prefixed_success_makes_exactly_one_unprovable(self) -> None:
        """A second completion-SHAPED success hidden behind a helper prefix: R2 cannot be
        decided from the parsable records -> LOST `record_framing_ambiguous`."""
        session, result, _prefix = self._dispatch("f015-ambig", first=SUCCESS, helper="progress: ", second=SUCCESS + "\n")
        self.assertEqual((result["state"], result["lost_reason"]), ("LOST", capture_mod.OUTCOME_RECORD_FRAMING_AMBIGUOUS), result)

    def test_the_only_record_prefixed_is_unprovable_never_completed(self) -> None:
        session, result, _prefix = self._dispatch("f015-only", first="", helper="progress: ", second=SUCCESS + "\n")
        self.assertEqual((result["state"], result["lost_reason"]), ("LOST", capture_mod.OUTCOME_RECORD_FRAMING_AMBIGUOUS), result)

    def test_the_clean_control_still_completes_and_a_clean_refusal_still_fails(self) -> None:
        _s, result, _p = self._dispatch("f015-clean", first=SUCCESS, helper="", second="")
        self.assertEqual(result["state"], "COMPLETED", result)
        _s, result, _p = self._dispatch("f015-refuse", first=SUCCESS, helper="", second=REFUSAL + "\n")
        self.assertEqual((result["state"], result["verdict"]["reason"]), ("FAILED", capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY))

    def test_prose_braces_without_a_completion_shape_do_not_widen_the_candidates(self) -> None:
        _s, result, _p = self._dispatch("f015-prose", first=SUCCESS, helper='progress: {"note": "half done", "pct": 50}\n', second="")
        self.assertEqual(result["state"], "COMPLETED", result)

    def test_the_embedded_object_scanner_is_string_and_escape_aware(self) -> None:
        text = 'x {"a": "}{", "b": "\\\\"} y {"type": "result", "is_error": true} {"broken": '
        objs = capture_mod.embedded_objects(text)
        self.assertEqual(objs, [{"a": "}{", "b": "\\"}, {"type": "result", "is_error": True}])
        runs = capture_mod.unparsable_runs('{"type": "ok"}\nprogress: {"type": "result",\n "is_error": true}\n{"type": "other"}\nleft\n')
        self.assertEqual(runs, ['progress: {"type": "result",\n "is_error": true}', "left"])



# =====================================================================================
# F-014 -- Linux: the subreaper is verified BEFORE the root is released
# =====================================================================================
class F014SubreaperBeforeGateTests(_HelperCase):
    """The reviewer's pre-setup fork/reap cut delayed ONLY `_set_subreaper` until the root had
    forked B, B forked G and the root reaped B.  Under the correction the root cannot run until
    the receipt exists: the ordering is enforced by `spawn`'s gate, verified here by recording
    the receipt's instant against the root's first act."""

    @LINUX_ONLY
    def test_the_receipt_precedes_the_roots_first_act_and_is_positive(self) -> None:
        marks = self.room.path / "marks.jsonl"
        real = pty_supervisor._set_subreaper

        def recorded():
            receipt = real()
            with marks.open("a") as f:
                f.write(json.dumps({"who": "watcher", "receipt": receipt, "t": time.monotonic_ns()}) + "\n")
            return receipt
        root = self.room.path / "root-f014.py"
        root.write_text(
            "import os,json,time\n"
            f"open({str(marks)!r},'a').write(json.dumps(dict(who='root', t=time.monotonic_ns()))+'\\n')\n"
            "os.write(1,(json.dumps(dict(type='result',is_error=False,session_id=os.environ['OS48_TEST_SID']))+'\\n').encode());os._exit(0)\n")
        with patch.object(pty_supervisor, "_set_subreaper", recorded):
            session, _sentinel = spawn_session(self.room, "", run_id="f014-order", argv=[PYTHON, str(root)], image=PYTHON,
                                               binding_mode="session_field", binding_field="session_id")
            self.assertEqual(session.await_completion()["state"], "COMPLETED")
        rows = [json.loads(x) for x in marks.read_text().splitlines()]
        self.assertEqual([r["who"] for r in rows], ["watcher", "root"], rows)
        self.assertEqual(rows[0]["receipt"], "subreaper")
        self.assertLess(rows[0]["t"], rows[1]["t"])
        state = self._state(session)["discovery"]
        self.assertEqual(state["root_watch"], "subreaper")
        self.assertEqual(session.membership_residual()["discovery"], "readable")

    @LINUX_ONLY
    def test_a_failed_or_unverifiable_setup_is_the_named_residual(self) -> None:
        for receipt in ("ownership_setup_unverified:set:EPERM", "ownership_setup_unverified:readback:0"):
            with self.subTest(receipt=receipt):
                self.room = Room()
                self.addCleanup(self.room.close)
                self.info = self.room.path / "helper.json"
                with patch.object(pty_supervisor, "_set_subreaper", lambda: receipt):
                    session, _sentinel = self._run(f"f014-{receipt.split(':')[1]}")
                residual = session.membership_residual()
                self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)
                entry = [u for u in residual["unknown"] if u.get("discovery") == "unreadable"][0]
                self.assertEqual(entry["root_watch"], receipt)
                self.assertIn(f"ownership_setup_unverified:{receipt}", entry["reasons"])
                session._reclaim(reason="lock-f014")
                rows = [r for r in session.journal.rows_for(session.intent_id) if r.get("event") == "descendants_unreaped"]
                self.assertEqual(rows[0]["source_vocabulary"]["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)
                self.assertEqual(pty_supervisor.proc_start_ticks(self.helper), self.helper_start, "signalled")

    @LINUX_ONLY
    def test_the_reviewers_delayed_setup_cut_can_no_longer_start_a_root(self) -> None:
        """`probe_linux_pre_subreaper_gap_real` delays `_set_subreaper` until the root has
        forked: under the correction the root is held on the gate until the receipt exists, so
        the delay wedges the leader instead of leaking a fork -- a named spawn refusal, never a
        clean residual."""
        from scripts.deterministic_workflow.standalone_pty import SpawnHandoffFailed
        real = pty_supervisor._set_subreaper
        ready = self.room.path / "root-forked"

        def delayed():
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.005)
            if not ready.exists():
                raise AssertionError("the root never forked before the setup: the gate holds it")
            return real()
        root = self.room.path / "root-f014c.py"
        root.write_text(f"import os\nopen({str(ready)!r},'w').write('x')\nos._exit(0)\n")
        with patch.object(pty_supervisor, "_set_subreaper", delayed):
            with self.assertRaises(SpawnHandoffFailed):
                spawn_session(self.room, "", run_id="f014-cut", argv=[PYTHON, str(root)], image=PYTHON,
                              binding_mode="session_field", binding_field="session_id")
        self.assertFalse(ready.exists(), "the root ran before the ownership setup")


# =====================================================================================
# F-016 -- Linux: fixed objects (pidfds) and lifetimes
# =====================================================================================
def _ns_last_pid_writable() -> bool:
    try:
        with open("/proc/sys/kernel/ns_last_pid", "r+") as f:
            f.read()
        return sys.platform == "linux" and os.getpid() == 1
    except OSError:
        return False


PRIVATE_NS = unittest.skipUnless(_ns_last_pid_writable(), "needs PID 1 of a private PID namespace with a writable ns_last_pid")


def _set_last_pid(value: int) -> None:
    with open("/proc/sys/kernel/ns_last_pid", "w") as f:
        f.write(str(value))


class F016FixedObjectTests(unittest.TestCase):
    """Reviewer's alias constructions under the fixed-object model.  `_Membership` is driven
    directly (as the reviewer did) with the REAL discover / note_reaped / close / residual
    reader; identity questions about cached members go to the held pidfd."""

    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)

    def _membership(self, root: int):
        m = pty_supervisor._Membership(os.fsencode(self.room.path / "members.jsonl"), fence="model:i",
                                       boot_id=pty_supervisor.host_boot_id(), agent_pid=root,
                                       agent_start_id=pty_supervisor.proc_start_ticks(root), root_watch="subreaper")
        self.addCleanup(m.close)
        return m

    @LINUX_ONLY
    def test_a_cached_unexited_member_whose_pidfd_died_never_adopts_a_new_holder_of_its_pid(self) -> None:
        """The unexited-cache path: the member's process is gone (pidfd readable) -> the
        cached tuple is EXITED (P5 through the fixed object), never a parent witness; a
        candidate naming that pid as parent is attributed only through P3/P4 of the LIVE
        holder."""
        root = os.fork()
        if root == 0:
            time.sleep(30); os._exit(0)
        self.addCleanup(_quiet, lambda: os.kill(root, signal.SIGKILL), lambda: os.waitpid(root, 0))
        m = self._membership(root)
        gone_r, gone_w = os.pipe()
        child = os.fork()
        if child == 0:
            os.read(gone_r, 1); os._exit(0)
        info = pty_supervisor._process_info(child)
        self.assertTrue(m._add(child, info[1], pty_supervisor.MEMBER_ROLE_DESCENDANT, "fixture"))
        key = next(k for k in m.members if k[0] == child)
        self.assertEqual(m._pidfd_state(key), "alive")
        self.assertIs(m._live_member_for(child), m.members[key])
        os.write(gone_w, b"x"); os.waitpid(child, 0)                  # the process ends and is reaped
        self.assertEqual(m._pidfd_state(key), "exited")
        self.assertIsNone(m._live_member_for(child), "a dead fixed object was accepted as a parent witness")
        self.assertTrue(m.members[key]["exited"])
        exit_rows = [r for r in pty_supervisor.read_ledger(m.path)["records"] if r.get("event") == "exited" and r["identity"]["pid"] == child]
        self.assertEqual(exit_rows[0]["observed_via"], "pidfd_exit")

    @LINUX_ONLY
    def test_a_cached_exited_key_never_suppresses_a_live_candidate(self) -> None:
        """The exited-cache path with a forced key alias: the ledger holds (pid, start,
        lifetime 1) EXITED; a live process presents the same (pid, start) -> attributed as
        lifetime 2 through its parentage, with its own pidfd."""
        root = os.fork()
        if root == 0:
            time.sleep(30); os._exit(0)
        self.addCleanup(_quiet, lambda: os.kill(root, signal.SIGKILL), lambda: os.waitpid(root, 0))
        m = self._membership(root)
        hold_r, hold_w = os.pipe()
        child = os.fork()
        if child == 0:
            os.read(hold_r, 1); os._exit(0)
        self.addCleanup(_quiet, lambda: os.write(hold_w, b"x"), lambda: os.waitpid(child, 0))
        ppid, start = pty_supervisor._process_info(child)
        # the alias: an EXITED lifetime 1 under exactly this child's key (as the reviewer's
        # ns_last_pid construction produces natively; here the ledger state is the fixture)
        stale = {"schema": pty_supervisor.MEMBER_SCHEMA, "event": pty_supervisor.MEMBER_EVENT_OBSERVED,
                 "identity": pty_supervisor._member_identity(child, start, pty_supervisor.host_boot_id(), "model:i"),
                 "role": pty_supervisor.MEMBER_ROLE_DESCENDANT, "observed_via": "fixture_old_lifetime", "pgid": 0,
                 "lifetime": 1, "fixed_object": "pidfd", "exited": True}
        m.members[(child, start, 1)] = stale
        with patch.object(m, "_list_candidates", return_value=([child], "")):
            added = m.discover("alias")
        self.assertEqual(added, 1, m.discovery)
        self.assertIn((child, start, 2), m.members)
        self.assertFalse(m.members[(child, start, 2)].get("exited"))
        self.assertEqual(m._pidfd_state((child, start, 2)), "alive")

    @LINUX_ONLY
    def test_the_residual_reader_keeps_one_entry_per_lifetime(self) -> None:
        from scripts.os48_lock_support import sh_profile
        from scripts.deterministic_workflow import standalone_journal as journal_mod
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        session = StandaloneSession(intent={"intent_id": "i-life", "run_id": "life", "role": "WORKER"},
                                    profile=sh_profile(str(self.room.path)), artifact_base=self.room.path / "art",
                                    run_id="life", journal=journal_mod.ExecutionJournal(self.room.path / "art", "life"))
        os.makedirs(os.path.dirname(str(session.capture.path)), exist_ok=True)
        live = os.fork()
        if live == 0:
            time.sleep(30); os._exit(0)
        self.addCleanup(_quiet, lambda: os.kill(live, signal.SIGKILL), lambda: os.waitpid(live, 0))
        start = pty_supervisor.proc_start_ticks(live)
        path = session._members_path()
        ident = pty_supervisor._member_identity(live, start, pty_supervisor.host_boot_id(), session.fence)
        rows = [{"schema": pty_supervisor.MEMBER_SCHEMA, "event": "observed", "identity": ident, "role": "descendant", "observed_via": "t", "pgid": 0, "lifetime": 1, "fixed_object": "pidfd"},
                {"schema": pty_supervisor.MEMBER_SCHEMA, "event": "exited", "identity": ident, "role": "descendant", "observed_via": "pidfd_exit", "pgid": 0, "lifetime": 1},
                {"schema": pty_supervisor.MEMBER_SCHEMA, "event": "observed", "identity": ident, "role": "descendant", "observed_via": "t", "pgid": 0, "lifetime": 2, "fixed_object": "pidfd"}]
        for r in rows:
            pty_supervisor._append_member(path, r)
        Path(os.fsdecode(path) + ".state.json").write_text(json.dumps({"schema": "os48.member.v1.state", "appended": 3, "failed": 0, "members": 2,
                                                                     "discovery": {"passes": 1, "listing_unreadable": 0, "listing_unstable": 0, "candidates_unreadable": 0, "forks_coalesced": 0, "watch_gaps": 0, "parents_unreadable": 0, "unobservable": 0, "root_watch": "subreaper", "reasons": [], "pids": []}}))
        residual = session.membership_residual()
        alive = [a for a in residual["alive"] if a["pid"] == live]
        self.assertEqual([(a["lifetime"], a.get("lifetime_binding")) for a in alive], [(2, "tick_granular")], residual)
        self.assertEqual([e["lifetime"] for e in residual["exited_incarnations"] if e["pid"] == live], [1])

    @PRIVATE_NS
    def test_native_same_pid_same_tick_reuse_is_a_new_lifetime(self) -> None:
        """The reviewer's ns_last_pid construction, natively (PID 1 of a private namespace):
        old (pid X, tick T) is adopted, reaped and marked exited; the allocator is reset so a
        NEW helper gets pid X at tick T -> discover attributes lifetime 2 (own pidfd), its
        grandchild is attributed, and close names them alive (`watch_ended`)."""
        receipt = pty_supervisor._set_subreaper()
        self.assertEqual(receipt, "subreaper")
        hz = os.sysconf("SC_CLK_TCK")
        for attempt in range(50):
            m = self._membership(os.getpid())
            hold_r, hold_w = os.pipe()
            tick = int(time.clock_gettime(time.CLOCK_BOOTTIME) * hz)
            while int(time.clock_gettime(time.CLOCK_BOOTTIME) * hz) == tick:
                pass
            old = os.fork()
            if old == 0:
                os.read(hold_r, 1); os._exit(0)
            old_info = pty_supervisor._process_info(old)
            with patch.object(m, "_list_candidates", return_value=([old], "")):
                m.discover("old")
            self.assertIn((old, old_info[1], 1), m.members)
            os.write(hold_w, b"x"); os.waitpid(old, 0)
            m.note_reaped(old)
            self.assertTrue(m.members[(old, old_info[1], 1)]["exited"])
            _set_last_pid(old - 1)
            new = os.fork()
            if new == 0:
                os.read(hold_r, 1); os._exit(0)
            new_info = pty_supervisor._process_info(new)
            if new != old or new_info[1] != old_info[1]:
                os.write(hold_w, b"x"); os.waitpid(new, 0); m.close()
                continue                                           # the tick moved: retry
            with patch.object(m, "_list_candidates", return_value=([new], "")):
                added = m.discover("new")
            self.assertEqual(added, 1, m.discovery)
            self.assertIn((new, new_info[1], 2), m.members)
            self.assertFalse(m.members[(new, new_info[1], 2)].get("exited"))
            m.close()
            state = json.loads(Path(os.fsdecode(m.path) + ".state.json").read_text())
            self.assertIn("watch_ended:members_alive_at_watcher_exit", state["discovery"]["reasons"])
            os.write(hold_w, b"x"); os.waitpid(new, 0)
            return
        self.skipTest("could not force a same-pid same-tick reuse in 50 attempts")


if __name__ == "__main__":
    unittest.main()
