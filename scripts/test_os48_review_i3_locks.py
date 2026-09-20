"""OS-48 locks for REVIEW_IMPLEMENTATION_iteration3.md (F-001 / F-004 / F-006 / F-008), each
re-running the reviewer's i3 construction through the REAL caller
(`evidence/review_implementation_i3/probe_*`):

* F-001 (a) R3's `sidecar_file` presence fact is the IMMUTABLE fence snapshot (the owner's
  reap-step read): a sidecar created or removed after N never changes the verdict -- supervisor,
  orphan and adoption paths; (b) sidecar CONTENT is never a settlement body source (option ii:
  refused by name `sidecar_unproven`, the body comes from the stream), so a helper update in
  the read->marker window changes nothing; the installed Codex profile carries its final body
  on the stream (`item.completed` / `agent_message`);
* F-004 the reviewer's birth-during-fill construction is `listallpids_unstable` (never accepted):
  completeness needs exact three-way agreement, an intersection is never completeness;
* F-006 a whole-line ledger prefix shorter than the durable append count, or accounting that
  changes between reads, is `membership_unreadable` with the unknown remainder named;
* F-008 a denied / failing sidecar read at the owner's snapshot is `sidecar_unreadable` (with the
  errno), ENOENT is `absent`; a `sidecar_file` binding cannot complete on either.
"""
from __future__ import annotations

import builtins
import ctypes
import errno
import json
import os
import shlex
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.deterministic_workflow import standalone_capture as capture_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_pty as pty_supervisor  # noqa: E402
from scripts.deterministic_workflow import standalone_runtime as rt  # noqa: E402
from scripts.deterministic_workflow.standalone_profile import ResultBodySelector  # noqa: E402
from scripts.os48_cut_harness import (Pause, fence_path, kill_child, read_fence, settle,  # noqa: E402
                                      successor, supervisor_child, wait_for)
from scripts.os48_lock_support import PYTHON, Room, spawn_session  # noqa: E402

DARWIN_ONLY = unittest.skipUnless(sys.platform == "darwin", "OS-48 libproc evidence seams are darwin's (probe_03)")
BODY_SELECTOR = (ResultBodySelector(channel="structured", record_type="result", body_field="result"),)


def _bound_root(room: Room, sidecar: Path, go: Path, ack: Path, *, action: str, initial: str | None) -> Path:
    """A bound root (`result` + session id) whose cooperative helper, on ``go``, CREATES /
    REMOVES / REWRITES the declared sidecar and acknowledges.  ``initial`` = the file's content
    written by the root before its completion record (None = no file)."""
    agent = room.path / f"root_{action}.py"
    body = ""
    if initial is not None:
        body += "open(%r,'w').write(%r)\n" % (str(sidecar), initial)
    body += ("os.write(1,(json.dumps(dict(type='result',is_error=False,session_id=os.environ['OS48_TEST_SID'],"
             "result='STREAM BODY'))+'\\n').encode())\n")
    if action == "create":
        act = "open(%r,'w').write('LATE SIDECAR')\n" % str(sidecar)
    elif action == "remove":
        act = "os.unlink(%r)\n" % str(sidecar)
    else:
        act = "open(%r,'w').write('REWRITTEN AFTER N')\n" % str(sidecar)
    agent.write_text("import os,time,json\n" + body +
                     "if os.fork()==0:\n"
                     "    while not os.path.exists(%r): time.sleep(.005)\n"
                     "    %s"
                     "    open(%r,'w').write('done')\n"
                     "    time.sleep(3);os._exit(0)\n"
                     "os._exit(0)\n" % (str(go), act, str(ack)))
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
# F-001 -- R3 presence from the immutable snapshot; sidecar content never a body source
# =====================================================================================
class F001ImmutableSidecarBindingTests(_RoomCase):
    def _session(self, run_id: str, *, action: str, initial: str | None):
        sidecar, go, ack = self.room.path / "last_message.md", self.room.path / f"{run_id}.go", self.room.path / f"{run_id}.ack"
        agent = _bound_root(self.room, sidecar, go, ack, action=action, initial=initial)
        session, sentinel = spawn_session(self.room, "", run_id=run_id, argv=[PYTHON, str(agent)], image=PYTHON,
                                          binding_mode="sidecar_file", binding_field="session_id",
                                          pump_until_sentinel=False, sidecar_path=str(sidecar))
        session.profile = replace(session.profile, output_last_message_path=str(sidecar), result_body_records=BODY_SELECTOR)
        session.driver = rt.drivers.driver_for(session.profile)
        return session, sentinel, sidecar, go, ack

    def test_a_sidecar_created_after_n_never_completes_a_sidecar_bound_dispatch(self) -> None:
        """`probe_late_sidecar_binding`: absent at the owner's read -> LOST `provenance_unbound`;
        the helper creates the file after N (acknowledged) -> the SAME fence, the SAME
        verdict (LOST) -- R3 reads the immutable snapshot, never the live path."""
        session, _sentinel, sidecar, go, ack = self._session("f001a", action="create", initial=None)
        before = session.await_completion()
        self.assertEqual((before["state"], before.get("lost_reason")), ("LOST", capture_mod.OUTCOME_PROVENANCE_UNBOUND), before)
        fence_before = Path(os.fsdecode(session._fence_path())).read_bytes()
        self.assertEqual(session._boundary["fence"]["sidecar"]["state"], capture_mod.SIDECAR_STATE_ABSENT)
        go.write_text("go")
        wait_for(ack.exists, seconds=5, what="the helper's late creation")
        self.assertTrue(sidecar.exists() and sidecar.stat().st_size > 0)
        after = session.await_completion()
        self.assertEqual((after["state"], after.get("lost_reason")), ("LOST", capture_mod.OUTCOME_PROVENANCE_UNBOUND), after)
        self.assertEqual(Path(os.fsdecode(session._fence_path())).read_bytes(), fence_before)

    def test_a_sidecar_removed_after_n_never_changes_a_completed_verdict(self) -> None:
        session, _sentinel, sidecar, go, ack = self._session("f001b", action="remove", initial="AT N")
        before = session.await_completion()
        self.assertEqual(before["state"], "COMPLETED", before)
        self.assertEqual(session._boundary["fence"]["sidecar"]["state"], capture_mod.SIDECAR_STATE_PRESENT)
        go.write_text("go")
        wait_for(ack.exists, seconds=5, what="the helper's late removal")
        self.assertFalse(sidecar.exists())
        after = session.await_completion()
        self.assertEqual(after["state"], "COMPLETED", after)
        out = settle(session, after)
        self.assertEqual(out["event"]["result"]["body"], "STREAM BODY", out)   # the body is the stream's

    def test_the_body_comes_from_the_stream_and_the_sidecar_source_is_refused_by_name(self) -> None:
        """`probe_snapshot_read_window` (b): whatever the file says -- rewritten in the
        read->marker window or after N -- the settled body is the in-boundary stream record;
        the provenance names the sidecar source refusal."""
        session, _sentinel, _sidecar, go, ack = self._session("f001c", action="rewrite", initial="EARLY BODY")
        completion = session.await_completion()
        self.assertEqual(completion["state"], "COMPLETED", completion)
        go.write_text("go")
        wait_for(ack.exists, seconds=5, what="the helper's rewrite")
        out = settle(session, completion)
        self.assertEqual(out["event"]["result"]["body"], "STREAM BODY", out)
        rows = [r for r in session.journal.rows_for(session.intent_id) if r["kind"] == "SETTLEMENT_OBSERVED"]
        prov = rows[-1]["source_vocabulary"]["result_body_provenance"]
        self.assertNotEqual(rows[-1]["source_vocabulary"]["result_body_source"], "output_last_message_path")
        self.assertIsNone(prov.get("sidecar_frozen_at_boundary"))

    def test_a_record_without_an_in_stream_body_falls_back_to_the_interval_never_the_file(self) -> None:
        session, _sentinel, _sidecar, _go, _ack = self._session("f001d", action="rewrite", initial="FILE BODY")
        session.profile = replace(session.profile, result_body_records=(
            ResultBodySelector(channel="structured", record_type="result", body_field="missing_field"),))
        session.driver = rt.drivers.driver_for(session.profile)
        completion = session.await_completion()
        self.assertEqual(completion["state"], "COMPLETED", completion)
        out = settle(session, completion)
        self.assertNotIn("FILE BODY", out["event"]["result"]["body"], out)
        rows = [r for r in session.journal.rows_for(session.intent_id) if r["kind"] == "SETTLEMENT_OBSERVED"]
        prov = rows[-1]["source_vocabulary"]["result_body_provenance"]
        self.assertEqual(prov.get("sidecar_refused"), capture_mod.SIDECAR_STATE_UNPROVEN, prov)
        self.assertEqual(prov.get("sidecar_presence"), capture_mod.SIDECAR_STATE_PRESENT)

    def _orphan(self, run_id: str, *, action: str, initial: str | None):
        sidecar, go, ack = self.room.path / "last_message.md", self.room.path / f"{run_id}.go", self.room.path / f"{run_id}.ack"
        agent = _bound_root(self.room, sidecar, go, ack, action=action, initial=initial)

        def install(pause: Pause, sup: int) -> None:
            pause.wrap(capture_mod, "write_capture_fence", run_id, supervisor_pid=sup)
        info = supervisor_child(self.room, run_id=run_id, cut=run_id, install=install,
                                agent=shlex.quote(PYTHON) + " " + shlex.quote(str(agent)) + "\nexit 0\n",
                                sidecar_path=str(sidecar))
        wait_for(Pause(self.room.path).file(run_id).exists, seconds=20, what="the marker before S's publication")
        kill_child(info)
        wait_for(fence_path(info).exists, seconds=20, what="the watcher's fence")
        return info, sidecar, go, ack

    def _successor(self, info, sidecar):
        s = successor(self.room, info)
        s.last_message_path = str(sidecar)
        s.profile = replace(s.profile, output_last_message_path=str(sidecar), result_body_records=BODY_SELECTOR,
                            completion_records=(replace(s.profile.completion_records[0], binding_mode="sidecar_file",
                                                        binding_field="session_id"),))
        s.driver = rt.drivers.driver_for(s.profile)
        return s

    def test_the_orphan_fence_pins_the_presence_fact_for_every_successor(self) -> None:
        """`probe_orphan_late_sidecar_binding`: S SIGKILLed before its fence; the watcher's fence
        records `absent`; the helper creates the file; the successor is LOST (unbound) and a
        later successor is LOST too -- the fence never changes."""
        info, sidecar, go, ack = self._orphan("f001o", action="create", initial=None)
        self.assertEqual(read_fence(info)["record"]["sidecar"]["state"], capture_mod.SIDECAR_STATE_ABSENT)
        first = settle(self._successor(info, sidecar))
        self.assertEqual((first["state"], first["completion"].get("lost_reason")), ("LOST", capture_mod.OUTCOME_PROVENANCE_UNBOUND), first)
        go.write_text("go")
        wait_for(ack.exists, seconds=5, what="the helper's late creation")
        second = settle(self._successor(info, sidecar))
        self.assertEqual((second["state"], second["completion"].get("lost_reason")), ("LOST", capture_mod.OUTCOME_PROVENANCE_UNBOUND), second)

    def test_the_orphan_fence_present_at_n_survives_a_later_removal(self) -> None:
        info, sidecar, go, ack = self._orphan("f001p", action="remove", initial="AT N")
        self.assertEqual(read_fence(info)["record"]["sidecar"]["state"], capture_mod.SIDECAR_STATE_PRESENT)
        go.write_text("go")
        wait_for(ack.exists, seconds=5, what="the helper's late removal")
        out = settle(self._successor(info, sidecar))
        self.assertEqual(out["state"], "COMPLETED", out)
        self.assertEqual(out["event"]["result"]["body"], "STREAM BODY")

    def test_the_installed_codex_profile_extracts_its_body_from_the_stream(self) -> None:
        """Option (ii) consequence for Codex: the real profile's body selector is the stream's
        `item.completed` / `agent_message` / `item.text`; the `-o` path is only the R3 presence
        fact.  (The real `codex exec --json` stream is measured in `i4_real_cli_smoke`.)"""
        from scripts import os37_r10_real_agent as real
        profile = real.codex_profile(worktree=str(self.room.path), codex_home=str(self.room.path / "ch"))
        selector = profile.result_body_records[0]
        self.assertEqual((selector.record_type, selector.item_type, selector.body_field),
                         ("item.completed", "agent_message", "item.text"))
        driver = rt.drivers.driver_for(profile)
        stream = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "FINAL"}}) + "\n"
        self.assertEqual(driver.result_body(stream, allow_path=False)["body"], "FINAL")
        self.assertEqual(driver.result_body("", allow_path=False)["source"], "whole_transcript")


# =====================================================================================
# F-004 -- birth during a partial fill is never accepted
# =====================================================================================
@DARWIN_ONLY
class F004BirthDuringFillTests(unittest.TestCase):
    def test_a_partial_fill_that_omits_a_process_born_before_the_fill_is_unstable(self) -> None:
        """The reviewer's construction: a real child is born between the independent walk and
        the libproc fill; the (fault-injected) fill omits exactly its entry.  The three
        enumerations disagree -> `listallpids_unstable`, `ids is None` -- an intersection is
        never treated as completeness."""
        real = pty_supervisor._LIBPROC
        state: dict = {}
        r, w = os.pipe()

        class Fault:
            def __getattr__(self, name):
                return getattr(real, name)

            def proc_listallpids(self, buf, size):
                if buf is None:
                    n = real.proc_listallpids(None, 0)
                    if "child" not in state:
                        child = os.fork()
                        if child == 0:
                            os.close(w)
                            os.read(r, 1)
                            os._exit(0)
                        state["child"] = child
                    return n
                full = (ctypes.c_int32 * (size // 4))()
                n = real.proc_listallpids(full, size)
                ids = [int(full[i]) for i in range(n) if int(full[i]) != state["child"]]
                state["child_in_actual_fill"] = state["child"] in [int(full[i]) for i in range(n)]
                for i, pid in enumerate(ids):
                    buf[i] = pid
                return len(ids)
        try:
            with patch.object(pty_supervisor, "_LIBPROC", Fault()):
                ids, why = pty_supervisor._libproc_list_all_pids()
        finally:
            os.write(w, b"x")
            os.close(w)
            os.close(r)
            os.waitpid(state["child"], 0)
        self.assertTrue(state.get("child_in_actual_fill"), state)
        self.assertIsNone(ids)
        # first attempt: the child is in `after` only -> changing evidence (`unstable`); a retry
        # sees it in both walks and still absent from the fill -> a MISSED entry (`partial`).
        # Either way a named refusal, never a list.
        self.assertIn(why, ("listallpids_unstable", "listallpids_partial"))

    def test_the_single_attempt_reading_of_a_birth_during_fill_is_unstable(self) -> None:
        """The reviewer's exact probe shape (its seam forks one child per count query): with
        one attempt the disagreement is `listallpids_unstable`."""
        real = pty_supervisor._LIBPROC
        state: dict = {}
        r, w = os.pipe()

        class Fault:
            def __getattr__(self, name):
                return getattr(real, name)

            def proc_listallpids(self, buf, size):
                if buf is None:
                    n = real.proc_listallpids(None, 0)
                    child = os.fork()
                    if child == 0:
                        os.close(w)
                        os.read(r, 1)
                        os._exit(0)
                    state["child"] = child
                    return n
                full = (ctypes.c_int32 * (size // 4))()
                n = real.proc_listallpids(full, size)
                ids = [int(full[i]) for i in range(n) if int(full[i]) != state["child"]]
                for i, pid in enumerate(ids):
                    buf[i] = pid
                return len(ids)
        try:
            with patch.object(pty_supervisor, "_LIBPROC", Fault()), \
                    patch.object(pty_supervisor, "_PIDLIST_MAX_ATTEMPTS", 1):
                ids, why = pty_supervisor._libproc_list_all_pids()
        finally:
            os.write(w, b"x")
            os.close(w)
            os.close(r)
            os.waitpid(state["child"], 0)
        self.assertEqual((ids, why), (None, "listallpids_unstable"))

    def test_exact_three_way_agreement_is_the_only_completeness(self) -> None:
        ids, why = pty_supervisor._libproc_list_all_pids()
        self.assertIsNone(why, why)
        self.assertEqual(set(ids), pty_supervisor._sysctl_all_pids())


# =====================================================================================
# F-006 -- readable content is joined to the durable append accounting
# =====================================================================================
class F006LedgerJoinTests(_RoomCase):
    def _detached(self, run_id: str):
        info = self.room.path / f"{run_id}.helper.json"
        agent = self.room.path / f"{run_id}.py"
        agent.write_text(
            "import os,time,json,signal\nr,w=os.pipe()\n"
            "if os.fork()==0:\n    os.close(r);os.setsid();signal.signal(signal.SIGHUP,signal.SIG_IGN)\n"
            "    open(%r,'w').write(json.dumps(dict(pid=os.getpid())))\n"
            "    os.write(w,b'r');os.close(w);time.sleep(30);os._exit(0)\n"
            "os.close(w);os.read(r,1);os.close(r)\ntime.sleep(0.3)\n"
            "os.write(1,(json.dumps(dict(type='result',is_error=False,session_id=os.environ['OS48_TEST_SID']))+'\\n').encode());os._exit(0)\n"
            % str(info))
        session, _sentinel = spawn_session(self.room, "", run_id=run_id, argv=[PYTHON, str(agent)], image=PYTHON,
                                           binding_mode="session_field", binding_field="session_id")
        helper = json.loads(info.read_text())["pid"]
        self.addCleanup(lambda: os.kill(helper, 9) if pty_supervisor._pid_presence(helper) != "absent" else None)
        self.assertEqual(session.await_completion()["state"], "COMPLETED")
        return session, helper

    def test_a_whole_line_short_read_is_membership_unreadable(self) -> None:
        """`probe_short_membership_ledger`: the ledger holds 3 complete lines (state: appended 3);
        a read seam returns only the first line -> `membership_unreadable`, the unknown
        remainder named (read 1 / appended 3), a residual row journaled."""
        session, helper = self._detached("f006a")
        path = session._members_path()
        full = Path(os.fsdecode(path)).read_bytes()
        first = full.split(b"\n")[0] + b"\n"
        self.assertGreaterEqual(full.count(b"\n"), 3)
        real_open = builtins.open

        class _Short:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return self.data
        def short(file, *a, **kw):
            if isinstance(file, (bytes, str, os.PathLike)) and os.fsencode(file) == path:
                return _Short(first)
            return real_open(file, *a, **kw)
        control = session.membership_residual()
        self.assertEqual([a["pid"] for a in control["alive"]], [helper], control)   # the unmodified read names the residual
        with patch.object(builtins, "open", side_effect=short):
            residual = session.membership_residual()
            session._reclaim(reason="lock")
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_MEMBERSHIP_UNREADABLE, residual)
        remainder = [u for u in residual["unknown"] if u.get("ledger") in ("incomplete", "changing")]
        self.assertTrue(remainder, residual)
        self.assertEqual((remainder[0]["read"], remainder[0]["appended"]), (1, int(residual["accounting"]["appended"])))
        rows = [r for r in session.journal.rows_for(session.intent_id) if r.get("event") == "descendants_unreaped"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_vocabulary"]["outcome"], capture_mod.OUTCOME_MEMBERSHIP_UNREADABLE)

    def test_changing_accounting_between_reads_is_membership_unreadable(self) -> None:
        session, _helper = self._detached("f006b")
        state_path = os.fsdecode(session._members_path()) + ".state.json"
        original = Path(state_path).read_text()
        calls = {"n": 0}
        real_open = builtins.open

        def flapping(file, *a, **kw):
            if isinstance(file, (bytes, str, os.PathLike)) and os.fspath(file) == state_path:
                calls["n"] += 1
                if calls["n"] == 2:
                    Path(state_path).write_text(json.dumps(dict(json.loads(original), appended=99)))
                else:
                    Path(state_path).write_text(original)
            return real_open(file, *a, **kw)
        with patch.object(builtins, "open", side_effect=flapping):
            residual = session.membership_residual()
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_MEMBERSHIP_UNREADABLE, residual)
        self.assertTrue(any(u.get("ledger") in ("changing", "incomplete") for u in residual["unknown"]), residual)

    def test_the_full_consistent_read_keeps_the_live_helper_residual(self) -> None:
        session, helper = self._detached("f006c")
        residual = session.membership_residual()
        # superseded by OS-48 i7 (conservative model): the root FORKED, so the residual is
        # `descendants_unknown` (`fork_coalesced`) by design -- the positive member is still
        # listed alive and the ledger/accounting join is what this lock is about.
        # (Linux has no fork events: the subreaper attributes positively; pre-reclaim it is clean.)
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN if sys.platform == "darwin" else None, residual)
        self.assertNotIn("membership_unreadable", str(residual["unknown"]))
        self.assertEqual([a["pid"] for a in residual["alive"]], [helper])
        self.assertEqual(len(pty_supervisor.read_members(session._members_path())), int(residual["accounting"]["appended"]))


# =====================================================================================
# F-008 -- a denied sidecar read is not absence
# =====================================================================================
class F008SidecarReadDenialTests(_RoomCase):
    def _sidecar_bound(self, run_id: str, *, sidecar_exists: bool, deny: bool):
        sidecar = self.room.path / f"{run_id}.md"
        if sidecar_exists:
            sidecar.write_text("BODY AT N")
        real_open = os.open

        def denied(path, *a, **kw):
            if os.fsencode(path) == os.fsencode(str(sidecar)):
                raise PermissionError(errno.EACCES, "injected sidecar read denied")
            return real_open(path, *a, **kw)
        if deny:
            os.open = denied                       # inherited by the forked watcher
            self.addCleanup(setattr, os, "open", real_open)
        session, sentinel = spawn_session(
            self.room, 'printf \'{"type":"result","is_error":false,"session_id":"\'"$OS48_TEST_SID"\'"}\\n\'\nexit 0\n',
            run_id=run_id, binding_mode="sidecar_file", binding_field="session_id",
            pump_until_sentinel=False, sidecar_path=str(sidecar))
        if deny:
            os.open = real_open
        session.profile = replace(session.profile, output_last_message_path=str(sidecar))
        session.driver = rt.drivers.driver_for(session.profile)
        return session, sentinel, sidecar

    def test_a_denied_read_at_the_snapshot_is_unreadable_not_absent(self) -> None:
        """`probe_snapshot_read_window` read_denied: the file exists; the watcher's open is
        EACCES -> snapshot + fence `sidecar_unreadable` (errno named); a `sidecar_file`
        binding is LOST `provenance_unbound`, never COMPLETED; adoption reads the same state."""
        session, sentinel, sidecar = self._sidecar_bound("f008a", sidecar_exists=True, deny=True)
        result = session.await_completion()
        self.assertEqual((result["state"], result.get("lost_reason")), ("LOST", capture_mod.OUTCOME_PROVENANCE_UNBOUND), result)
        snap = capture_mod.read_sidecar_snapshot(session.capture.path, session.incarnation, fence=session.fence)
        self.assertEqual(snap["state"], capture_mod.SIDECAR_STATE_UNREADABLE, snap)
        self.assertIn("PermissionError", snap["record"]["error"])
        self.assertEqual(session._boundary["fence"]["sidecar"]["state"], capture_mod.SIDECAR_STATE_UNREADABLE)
        s = successor(self.room, self._info(session, sentinel))
        s.last_message_path = str(sidecar)
        s.profile = session.profile
        s.driver = rt.drivers.driver_for(s.profile)
        out = settle(s)
        self.assertEqual(out["state"], "LOST", out)
        self.assertEqual(s._sidecar_state, capture_mod.SIDECAR_STATE_UNREADABLE)

    def test_genuine_absence_is_absent_and_presence_is_present(self) -> None:
        session, _sentinel, _sidecar = self._sidecar_bound("f008b", sidecar_exists=False, deny=False)
        result = session.await_completion()
        self.assertEqual(result["state"], "LOST", result)
        snap = capture_mod.read_sidecar_snapshot(session.capture.path, session.incarnation, fence=session.fence)
        self.assertEqual((snap["state"], snap["record"]["error"]), (capture_mod.SIDECAR_STATE_ABSENT, ""))
        session2, _sentinel2, _sidecar2 = self._sidecar_bound("f008c", sidecar_exists=True, deny=False)
        self.assertEqual(session2.await_completion()["state"], "COMPLETED")
        self.assertEqual(session2._boundary["fence"]["sidecar"]["state"], capture_mod.SIDECAR_STATE_PRESENT)


if __name__ == "__main__":
    unittest.main()
