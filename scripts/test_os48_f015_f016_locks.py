"""OS-48 locks for REVIEW_IMPLEMENTATION_iteration8.md F-015 / F-016 (run_5fcd2beac376), each
through the REAL caller the reviewer used, RED at checkpoint c8f2747 and GREEN with the fix.

* F-015 (refusal scan fail-open).  The framing scan of the fenced range ``[baseline, N)`` is
  a BOUNDED NESTED traversal that examines inner objects and says when it could not finish:
  (a) the root's refusal after >= 4,096 harmless ``{}`` objects (`probe_scanner_bound_real`)
  is a refusal in the boundary -- FAILED `refusal_in_boundary`, never COMPLETED;
  (b) the root's refusal nested inside a cooperative wrapper -- a two-line ``{"progress": ``
  closed after the root's own newline-terminated record (`probe_nested_refusal_plain_root`,
  the serializer IDENTICAL between control and attack), a one-line parsable container, or a
  prose-prefixed wrapper -- is a refusal in the boundary;
  (c) a scan that hits its bound (objects / depth / bytes) before examining the whole range
  is the NAMED outcome `record_scan_incomplete` (LOST) -- never "no candidates", never
  COMPLETED.  A JSON string literal's contents are data, never a record; a nested object's
  generic ``is_error`` (a tool result inside a wrapper) is data too -- only a positive rule
  (declared completion type / declared auth marker) makes a nested object a refusal.
* F-016 (Linux PID-reuse ownership).  A pidfd acquired for a candidate may bind a DIFFERENT
  birth than the positively parented one the pre-acquisition reads described
  (`probe_linux_same_tick_foreign_admission`): admission now re-reads identity and
  parentage AFTER the fixed object is held and admits only the proven same incarnation --
  otherwise `candidate_identity_unverified`, NOT a member.  A reader with no held pidfd (the
  supervisor; recovery after the watcher died -- `probe_linux_same_tick_foreign_crash`)
  never reports alive/owned from (pid, tick) equality: the recorded pidfs inode against a
  fresh pidfd's is the independent binding, and without one the lifetime is `unknown` by
  name (`pid_tick_unverified`).  Darwin's reader states its binding (`start_microsecond`)
  and is not weakened.

* F-017 (run_7859f202457c; REVIEW_IMPLEMENTATION_iteration3 of run_5fcd2beac376, RED at
  checkpoint 709cea0).  A refusal `select_completion` has POSITIVELY selected over the verified
  `[baseline, N)` is never replaced by a reader failure that comes after the selection
  (`probe_reached_refusal_contract`): `completion()` no longer re-reads the range once the
  selector has returned, so a `MemoryError` at that point has nothing to overwrite -- FAILED
  `refusal_in_boundary` with the refusal provenance retained, never LOST
  `record_scan_incomplete`; a selected completion record likewise keeps its own verdict.

The same-tick constructions need PID 1 of a private PID namespace with a writable
``ns_last_pid`` (docker ``--privileged``), exactly as the reviewer ran them; the allocator
is the only thing the driver controls -- every kernel observation is real.
"""
from __future__ import annotations

import contextlib
import hashlib
import inspect
import json
import os
import signal
import sys
import time
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.deterministic_workflow import standalone_capture as capture_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_lifecycle as lifecycle  # noqa: E402
from scripts.deterministic_workflow import standalone_pty as pty_supervisor  # noqa: E402
from scripts.os48_lock_support import PYTHON, Room, sh_profile, spawn_session  # noqa: E402
from scripts.test_os48_review_i4_locks import _HelperCase  # noqa: E402
from scripts.test_os48_review_i7_locks import (_FRAMING_ROOT, REFUSAL, SUCCESS,  # noqa: E402
                                               _ns_last_pid_writable, _quiet, _set_last_pid)

DARWIN_ONLY = unittest.skipUnless(sys.platform == "darwin", "the microsecond start identity is darwin's")
LINUX_ONLY = unittest.skipUnless(sys.platform == "linux", "pidfds and the /proc discovery walk are Linux's")
#: declared HERE (not imported) so the CI-lane manifest's source reading resolves the gate
PRIVATE_NS = unittest.skipUnless(_ns_last_pid_writable(), "needs PID 1 of a private PID namespace with a writable ns_last_pid")

#: names the fix introduces, spelled as literals so a checkpoint without them fails on
#: BEHAVIOUR (a wrong state), never on a missing attribute
SCAN_INCOMPLETE = "record_scan_incomplete"
IDENTITY_UNVERIFIED = "candidate_identity_unverified"

#: The reviewer's plain-root construction (`probe_nested_refusal_plain_root`): the root writes
#: an early success, lets a child write PREFIX to inherited stdout (pipe-acknowledged), writes
#: its refusal with the SAME serializer in both cases, then lets the child close the wrapper.
_PLAIN_ROOT = """import os,json
sid=os.environ['OS48_TEST_SID']
os.write(1,(json.dumps(dict(type='result',is_error=False,session_id=sid,result='early body'))+'\\n').encode())
pr,pw=os.pipe(); rr,rw=os.pipe();child=os.fork()
if child==0:
 os.close(pr);os.close(rw)
 os.write(1,PREFIX)
 os.write(pw,b'prefix');os.close(pw)
 assert os.read(rr,1)==b'x'
 os.write(1,b'}\\n');os._exit(0)
os.close(pw);os.close(rr);assert os.read(pr,32)==b'prefix'
refusal=(json.dumps(dict(type='result',is_error=True,session_id=sid))+'\\n').encode()
os.write(1,refusal) # identical PLAIN root serializer in control and wrapper
os.write(rw,b'x');os.close(rw);os.waitpid(child,0);os._exit(0)
"""


class _FramingDispatch(unittest.TestCase):
    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)

    def _dispatch(self, run_id: str, *, first: str, helper: str, second: str, code: str = _FRAMING_ROOT):
        agent = self.room.path / f"root-{run_id}.py"
        agent.write_text(code % {"first": first, "helper": helper, "second": second})
        return self._run(run_id, agent)

    def _run(self, run_id: str, agent: Path):
        session, _sentinel = spawn_session(self.room, "", run_id=run_id, argv=[PYTHON, str(agent)], image=PYTHON,
                                           binding_mode="session_field", binding_field="session_id")
        result = session.await_completion()
        n = session._boundary["fence"]["boundary"]["offset_n"]
        return session, result, session.capture.raw()[:n]

    def _assert_refusal_in_boundary(self, result, prefix) -> None:
        self.assertEqual(result["state"], "FAILED", result)
        self.assertEqual(result["verdict"]["reason"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, result)
        self.assertIn(b'"is_error": true', prefix, "the refusal is not inside [0, N)")


# =====================================================================================
# F-015 -- the framing scan is bounded, nested, and honest about not finishing
# =====================================================================================
class F015ScannerBoundTests(_FramingDispatch):
    """`probe_scanner_bound_real` through the production spawn + `await_completion`."""

    def test_a_refusal_after_4096_harmless_objects_is_a_refusal_in_boundary(self) -> None:
        """4,095 objects before the refusal was FAILED at c8f2747; 4,096 and 4,097 were
        COMPLETED (the scanner stopped at 4,096 and reported nothing about the remainder)."""
        for count in (4096, 4097):
            with self.subTest(objects_before_refusal=count):
                _s, result, prefix = self._dispatch(f"f015-bound-{count}", first=SUCCESS,
                                                    helper="progress: " + "{} " * count, second=REFUSAL + "\n")
                self._assert_refusal_in_boundary(result, prefix)
                self.assertEqual(result["evidence"]["refusal"]["source"], "framing_ambiguous_refusal")

    def test_a_scan_that_hits_its_bound_is_named_incomplete_never_completed(self) -> None:
        """More harmless objects than the scan will examine, then the root's refusal: the scan
        cannot decide R1/R2 -> LOST `record_scan_incomplete` with the bound named; at c8f2747
        the exhausted scan was read as "no candidates" and the early success won."""
        limit = int(getattr(capture_mod, "EMBEDDED_SCAN_OBJECT_LIMIT", 4096))
        count = limit + 8
        session, result, prefix = self._dispatch("f015-exhaust", first=SUCCESS,
                                                 helper="progress: " + "{} " * count, second=REFUSAL + "\n")
        self.assertIn(b'"is_error": true', prefix)
        self.assertNotEqual(result["state"], "COMPLETED", result)
        self.assertEqual((result["state"], result["lost_reason"]), ("LOST", SCAN_INCOMPLETE), result)
        rows = [r for r in session.journal.rows_for(session.intent_id)
                if (r.get("source_vocabulary") or {}).get("provenance_outcome") == SCAN_INCOMPLETE]
        self.assertTrue(rows, "no journal row names the incomplete scan")
        scan = rows[-1]["source_vocabulary"].get("scan") or {}
        self.assertEqual((scan.get("complete"), scan.get("reason")), (False, "object_limit"), rows[-1])

    def test_the_named_outcome_is_registered_as_a_lost_reason(self) -> None:
        self.assertIn(SCAN_INCOMPLETE, lifecycle.LOST_REASONS)
        self.assertIn(SCAN_INCOMPLETE, lifecycle.OS48_LOST_OUTCOMES)
        self.assertEqual(lifecycle.resolve_unknown("os48_named", lost_reason=SCAN_INCOMPLETE)["lost_reason"], SCAN_INCOMPLETE)


class F015NestedWrapperTests(_FramingDispatch):
    """`probe_nested_refusal_plain_root` and the two nested cases of `probe_scanner_bound_real`."""

    def test_the_plain_root_refusal_inside_a_two_line_wrapper_is_a_refusal_in_boundary(self) -> None:
        """The serializer is IDENTICAL between control (`progress: `) and attack (`{"progress": `);
        only the cooperative child's prefix differs.  Both are FAILED `refusal_in_boundary`; the
        complete refusing object is inside [0, N) in both."""
        for name, prefix in (("control", b"progress: "), ("wrapper", b'{"progress": ')):
            with self.subTest(case=name):
                agent = self.room.path / f"plain-{name}.py"
                agent.write_text(_PLAIN_ROOT.replace("PREFIX", repr(prefix)))
                _s, result, fenced = self._run(f"f015-plain-{name}", agent)
                self._assert_refusal_in_boundary(result, fenced)
                self.assertIn(prefix + b'{"type": "result", "is_error": true', fenced)

    def test_a_one_line_container_and_a_prose_prefixed_wrapper_hide_no_refusal(self) -> None:
        cases = (("one-line", '{"progress": ', REFUSAL + "}\n"),                      # a parsable, undeclared container
                 ("prose-wrapper", "progress: ", '{"progress": ' + REFUSAL + "}\n"))  # an unparsable run with nesting
        for name, helper, second in cases:
            with self.subTest(case=name):
                _s, result, prefix = self._dispatch(f"f015-{name}", first=SUCCESS, helper=helper, second=second)
                self._assert_refusal_in_boundary(result, prefix)

    def test_a_wrapped_success_makes_exactly_one_unprovable_never_completed(self) -> None:
        """A second completion-SHAPED success nested in a container (one-line parsable, or a
        two-line wrapper): R2 cannot be decided -> LOST `record_framing_ambiguous`."""
        for name, helper, second in (("one-line", '{"progress": ', SUCCESS + "}\n"),
                                     ("two-line", '{"progress": ', SUCCESS + "\n}\n")):
            with self.subTest(case=name):
                _s, result, _p = self._dispatch(f"f015-wrapsucc-{name}", first=SUCCESS, helper=helper, second=second)
                self.assertEqual((result["state"], result["lost_reason"]),
                                 ("LOST", capture_mod.OUTCOME_RECORD_FRAMING_AMBIGUOUS), result)

    def test_string_literals_and_nested_tool_errors_are_data_not_records(self) -> None:
        """Positive-rule controls (GREEN before and after): a refusal SPELLED inside a JSON
        string literal is not a record, and a nested object's generic `is_error` (a tool
        result inside a wrapper) is not the dispatch's refusal.  The clean success completes."""
        quoted = json.dumps(REFUSAL)                                              # a string literal
        cases = (("string-in-run", 'progress: {"note": ' + quoted + "}\n"),
                 ("string-in-record", '{"note": ' + quoted + "}\n"),
                 ("nested-tool-error", '{"progress": {"type": "tool_result", "is_error": true, "content": "x"}}\n'))
        for name, helper in cases:
            with self.subTest(case=name):
                _s, result, _p = self._dispatch(f"f015-ctl-{name}", first=SUCCESS, helper=helper, second="")
                self.assertEqual(result["state"], "COMPLETED", result)


class F015ScannerUnitTests(unittest.TestCase):
    """The scanner's own contract (both copies through the mirror parity check)."""

    def _scan(self, text: str, **bounds):
        return capture_mod.embedded_scan(text, budget=capture_mod.ScanBudget(**bounds))

    def test_nested_objects_are_examined_and_string_literals_are_not(self) -> None:
        scan = self._scan('{"progress": {"type": "result", "is_error": true}\n}')
        self.assertTrue(scan["complete"], scan)
        self.assertIn({"type": "result", "is_error": True}, scan["objects"])
        scan = self._scan('{"progress": [{"a": 1}, {"type": "result", "is_error": true}]}')
        self.assertIn({"type": "result", "is_error": True}, scan["objects"])
        scan = self._scan('{"note": "{\\"type\\": \\"result\\", \\"is_error\\": true}"}')
        self.assertEqual(scan["objects"], [{"note": '{"type": "result", "is_error": true}'}], scan)

    def test_each_bound_ends_the_scan_incomplete_by_name(self) -> None:
        scan = self._scan("{} " * 10, objects=4)
        self.assertEqual((scan["complete"], scan["reason"], scan["examined"]), (False, "object_limit", 4), scan)
        scan = self._scan('{"a":' * 8 + "1" + "}" * 8, depth=3)
        self.assertEqual((scan["complete"], scan["reason"]), (False, "depth_limit"), scan)
        scan = self._scan("{" * 200, chars=64)
        self.assertEqual((scan["complete"], scan["reason"]), (False, "byte_budget"), scan)
        deep = '{"a":' * 5000 + "1" + "}" * 5000                          # past the JSON parser's own recursion
        scan = self._scan(deep)
        self.assertFalse(scan["complete"], scan["reason"])

    def test_the_budget_is_shared_across_runs_and_the_selector_names_it(self) -> None:
        from scripts.deterministic_workflow.standalone_drivers import driver_for
        profile = sh_profile("/tmp", binding_mode="session_field", binding_field="session_id")
        driver = driver_for(profile)
        sid = "s-1"
        success = json.dumps({"type": "result", "is_error": False, "session_id": sid})
        text = success + "\n" + "progress: " + "{} " * 6 + "\n" + "more: " + "{} " * 6 + "\n"
        with patch.object(capture_mod, "EMBEDDED_SCAN_OBJECT_LIMIT", 8):
            selection = driver.select_completion(text, bound_value=sid)
        self.assertEqual(selection["outcome"], SCAN_INCOMPLETE, selection)
        self.assertEqual(selection["scan"]["reason"], "object_limit")
        # a refusal the scan DID reach still dominates an incomplete remainder (R1)
        refusal = json.dumps({"type": "result", "is_error": True, "session_id": sid})
        text = success + "\nprogress: " + refusal + " " + "{} " * 20 + "\n"
        with patch.object(capture_mod, "EMBEDDED_SCAN_OBJECT_LIMIT", 8):
            selection = driver.select_completion(text, bound_value=sid)
        self.assertEqual(selection["outcome"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, selection)
        # complete and clean: the bound record is selected
        selection = driver.select_completion(success + "\n", bound_value=sid)
        self.assertEqual((selection["outcome"], selection["record"]["session_id"]), (None, sid), selection)


# =====================================================================================
# F-016 -- the fixed object binds the SAME lifetime the reads described
# =====================================================================================
def _send(fd: int, value) -> None:
    raw = json.dumps(value).encode() + b"\n"
    while raw:
        raw = raw[os.write(fd, raw):]


def _recv(fd: int):
    raw = b""
    while not raw.endswith(b"\n"):
        chunk = os.read(fd, 65536)
        if not chunk:
            raise RuntimeError("peer closed before ACK: " + repr(raw))
        raw += chunk
    return json.loads(raw)


def _kernel_binding(pid: int) -> dict:
    """The independent binding the fixed reader uses (`pidfd_binding`); computed here the same
    way when the tree under test predates it, so a checkpoint fails on BEHAVIOUR (what the
    reader reports), never on the missing attribute."""
    production = getattr(pty_supervisor, "pidfd_binding", None)
    if production is not None:
        return production(pid)
    try:
        fd, me = os.pidfd_open(pid), os.pidfd_open(os.getpid())
    except OSError:
        return {"state": "unavailable", "fixed_object_id": 0}
    try:
        ino, ref = os.fstat(fd).st_ino, os.fstat(me).st_ino
    finally:
        os.close(fd); os.close(me)
    if not ino or ino == ref:
        return {"state": "unavailable", "fixed_object_id": 0, "model": ""}
    return {"state": capture_mod.EVIDENCE_FINAL, "fixed_object_id": ino, "model": ""}


def _kill_own(pid: int) -> None:
    """Cleanup scoped to THIS process's own children (the driver's), never a foreign pid."""
    info = pty_supervisor._process_info(pid)
    if info and info[0] == os.getpid():
        _quiet(lambda: os.kill(pid, signal.SIGTERM), lambda: os.waitpid(pid, 0))


class _MembershipCase(unittest.TestCase):
    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)

    def _membership(self, root: int):
        m = pty_supervisor._Membership(os.fsencode(self.room.path / "members.jsonl"), fence="model:i",
                                       boot_id=pty_supervisor.host_boot_id(), agent_pid=root,
                                       agent_start_id=pty_supervisor.proc_start_ticks(root), root_watch="subreaper")
        self.addCleanup(m.close)
        return m

    def _held_child(self):
        r, w = os.pipe()
        child = os.fork()
        if child == 0:
            os.read(r, 1); os._exit(0)
        self.addCleanup(_quiet, lambda: os.write(w, b"x"), lambda: os.waitpid(child, 0))
        return child, w

    def _unverified_reasons(self, m) -> list[str]:
        return [r for r in m.discovery["reasons"] if r.startswith(IDENTITY_UNVERIFIED + ":")]


@LINUX_ONLY
class F016AdmissionVerificationTests(_MembershipCase):
    """The admission join, without a namespace: the seams change what the re-read / the
    acquisition sees for one candidate, everything else is the real kernel."""

    def test_a_candidate_whose_parentage_differs_after_acquisition_is_unverified(self) -> None:
        """Pre-read: the child is this subreaper's; the read AFTER the pidfd is held names
        another parent (what the reviewer's delayed acquisition binds) -> named
        `candidate_identity_unverified`, NOT a member, no pidfd retained."""
        m = self._membership(os.getpid())
        child, _w = self._held_child()
        real = pty_supervisor._process_info
        seen = {"n": 0}

        def rereads_differently(pid: int):
            info = real(pid)
            if pid == child and info is not None:
                seen["n"] += 1
                if seen["n"] >= 2:                     # every read AFTER the first (the re-read)
                    return (1, info[1])
            return info
        with patch.object(pty_supervisor, "_process_info", rereads_differently), \
                patch.object(m, "_list_candidates", return_value=([child], "")):
            added = m.discover("seam")
        self.assertEqual(added, 0, m.members)
        self.assertNotIn(child, [k[0] for k in m.members], "a candidate whose parentage changed after acquisition was admitted")
        self.assertEqual(self._unverified_reasons(m), [f"{IDENTITY_UNVERIFIED}:ppid:{os.getpid()}->1"], m.discovery)
        self.assertEqual(m.discovery.get("candidates_unverified"), 1, m.discovery)
        self.assertIn(child, m.discovery["pids"])
        self.assertFalse([k for k in m._pidfds if k[0] == child], "a pidfd was retained for a refused candidate")
        rows = [r for r in pty_supervisor.read_ledger(m.path)["records"] if r.get("kind") == IDENTITY_UNVERIFIED]
        self.assertEqual([r["pids"] for r in rows], [[child]], rows)

    def test_a_candidate_reaped_before_acquisition_is_never_admitted(self) -> None:
        """The acquisition is delayed until the positively parented child has EXITED and been
        reaped: `pidfd_open` answers ESRCH -> no fixed object -> not a member (c8f2747 admitted
        it with `fixed_object: none`)."""
        m = self._membership(os.getpid())
        child, w = self._held_child()
        real_open = os.pidfd_open

        def delayed_open(pid: int, *args, **kwargs):
            if pid == child:
                os.write(w, b"x"); os.waitpid(child, 0)      # ends and reaps the child FIRST
            return real_open(pid, *args, **kwargs)
        with patch.object(os, "pidfd_open", delayed_open), \
                patch.object(m, "_list_candidates", return_value=([child], "")):
            added = m.discover("seam")
        self.assertEqual(added, 0, m.members)
        self.assertNotIn(child, [k[0] for k in m.members])
        self.assertTrue([r for r in m.discovery["reasons"] if r.startswith("pidfd_unavailable:ProcessLookupError")], m.discovery)

    def test_a_verified_descendant_holds_its_pidfd_and_records_its_binding(self) -> None:
        """The control: a live child of this subreaper is admitted with its pidfd held; when
        the kernel's pidfd inodes are unique (pidfs) the record carries `fixed_object_id` --
        the binding a reader with no pidfd compares against."""
        m = self._membership(os.getpid())
        child, _w = self._held_child()
        with patch.object(m, "_list_candidates", return_value=([child], "")):
            self.assertEqual(m.discover("control"), 1, m.discovery)
        key = next(k for k in m.members if k[0] == child)
        self.assertEqual(m._pidfd_state(key), "alive")
        self.assertEqual(self._unverified_reasons(m), [])
        record = m.members[key]
        binding = _kernel_binding(child)
        if binding["state"] == capture_mod.EVIDENCE_FINAL:
            self.assertEqual(record.get("fixed_object_id"), binding["fixed_object_id"], record)
            self.assertEqual(record.get("fixed_object_id"), os.fstat(m._pidfds[key]).st_ino)
            # i2 (F-016): the binding is recorded WITH the proven inode model it is valid under
            self.assertEqual(record.get("fixed_object_model"), binding.get("model"), record)
            self.assertTrue(record.get("fixed_object_model"), record)
        else:
            self.assertNotIn("fixed_object_id", record, "an inode that is not unique was recorded as a binding")


@LINUX_ONLY
class F016RecoveryReaderTests(unittest.TestCase):
    """`membership_residual` with NO held pidfd (the supervisor / a recovery reader after the
    watcher died): the recorded lifetime is alive ONLY through an independent binding."""

    def setUp(self) -> None:
        from scripts.deterministic_workflow import standalone_journal as journal_mod
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        self.room = Room()
        self.addCleanup(self.room.close)
        self.session = StandaloneSession(intent={"intent_id": "i-bind", "run_id": "bind", "role": "WORKER"},
                                         profile=sh_profile(str(self.room.path)), artifact_base=self.room.path / "art",
                                         run_id="bind", journal=journal_mod.ExecutionJournal(self.room.path / "art", "bind"))
        os.makedirs(os.path.dirname(str(self.session.capture.path)), exist_ok=True)
        self.live = os.fork()
        if self.live == 0:
            time.sleep(30); os._exit(0)
        self.addCleanup(_quiet, lambda: os.kill(self.live, signal.SIGKILL), lambda: os.waitpid(self.live, 0))
        self.start = pty_supervisor.proc_start_ticks(self.live)

    def _ledger(self, **observed_extra) -> None:
        path = self.session._members_path()
        ident = pty_supervisor._member_identity(self.live, self.start, pty_supervisor.host_boot_id(), self.session.fence)
        row = {"schema": pty_supervisor.MEMBER_SCHEMA, "event": "observed", "identity": ident, "role": "descendant",
               "observed_via": "t", "pgid": 0, "lifetime": 1, "fixed_object": "pidfd", **observed_extra}
        pty_supervisor._append_member(path, row)
        Path(os.fsdecode(path) + ".state.json").write_text(json.dumps({
            "schema": "os48.member.v1.state", "appended": 1, "failed": 0, "members": 1,
            "discovery": {"passes": 1, "listing_unreadable": 0, "listing_unstable": 0, "candidates_unreadable": 0,
                          "forks_coalesced": 0, "watch_gaps": 0, "parents_unreadable": 0, "unobservable": 0,
                          "root_watch": "subreaper", "reasons": [], "pids": []}}))

    def _entry(self, residual, bucket: str):
        return [e for e in residual[bucket] if isinstance(e, dict) and e.get("pid") == self.live]

    def test_pid_and_tick_equality_alone_is_unknown_by_name_never_alive(self) -> None:
        """The recorded row has no independent binding (a watcher on a kernel without unique
        pidfd inodes, or a pre-fix ledger): the live process under the same (pid, tick) is
        `pid_tick_unverified` -- an UNKNOWN entry and `descendants_unknown`; at c8f2747 it was
        reported alive (`probe_linux_same_tick_foreign_crash`)."""
        self._ledger()
        residual = self.session.membership_residual()
        self.assertEqual(self._entry(residual, "alive"), [], residual)
        unknown = self._entry(residual, "unknown")
        self.assertEqual(len(unknown), 1, residual)
        self.assertEqual((unknown[0]["lifetime_binding"], unknown[0]["binding_detail"]),
                         ("pid_tick_unverified", "no_recorded_fixed_object_id"), unknown)
        self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)

    def test_a_recorded_binding_the_kernel_confirms_is_alive_and_a_different_one_is_gone(self) -> None:
        binding = _kernel_binding(self.live)
        if binding["state"] != capture_mod.EVIDENCE_FINAL:
            self.skipTest(f"this kernel offers no unique pidfd inode ({binding['state']}): the unknown lock above applies")
        # i2 (F-016): a recorded inode counts only together with the proven model it was
        # recorded under (a row with an id but no model -- the i1 ledger shape -- is UNKNOWN)
        self._ledger(fixed_object_id=binding["fixed_object_id"], fixed_object_model=binding["model"])
        residual = self.session.membership_residual()
        alive = self._entry(residual, "alive")
        self.assertEqual([(a.get("lifetime_binding"), a.get("binding_model")) for a in alive],
                         [("pidfs_inode", binding["model"])], residual)
        self.assertIsNone(residual["outcome"], residual)
        # the reviewer's crash construction in one row: the SAME (pid, tick) holds a DIFFERENT
        # birth (its inode differs) -> that lifetime is positively gone, never alive
        os.replace(os.fsdecode(self.session._members_path()), os.fsdecode(self.session._members_path()) + ".old")
        self._ledger(fixed_object_id=binding["fixed_object_id"] + 1, fixed_object_model=binding["model"])
        residual = self.session.membership_residual()
        self.assertEqual(self._entry(residual, "alive"), [], residual)
        self.assertEqual(self._entry(residual, "unknown"), [], residual)
        gone = [e for e in residual["exited_incarnations"] if e["pid"] == self.live]
        self.assertEqual([g["lifetime_binding"] for g in gone], ["pidfs_inode_mismatch"], residual)

    def test_a_reader_without_a_kernel_binding_is_unknown_even_with_a_recorded_one(self) -> None:
        self._ledger(fixed_object_id=12345, fixed_object_model="pidfs_struct_pid_64")
        with patch.object(pty_supervisor, "pidfd_binding", create=True,
                          return_value={"state": "unavailable", "fixed_object_id": 0, "model": "pidfs_struct_pid_64"}):
            residual = self.session.membership_residual()
        self.assertEqual(self._entry(residual, "alive"), [], residual)
        unknown = self._entry(residual, "unknown")
        self.assertEqual([(u["lifetime_binding"], u["binding_detail"]) for u in unknown],
                         [("pid_tick_unverified", "reader_binding:unavailable")], residual)


class F016RealDispatchBindingTests(_HelperCase):
    """The reviewer's detached-helper root through the production spawn: the reader's entry
    for the live helper STATES its binding on both platforms."""

    @LINUX_ONLY
    def test_the_linux_helper_is_alive_only_through_the_pidfs_binding_else_unknown(self) -> None:
        session, _sentinel = self._run("f016-real", ack="attributed")
        residual = session.membership_residual()
        entries = [m for m in residual["alive"] + [u for u in residual["unknown"] if isinstance(u, dict) and "pid" in u]
                   if m["pid"] == self.helper]
        self.assertEqual(len(entries), 1, residual)
        binding = _kernel_binding(self.helper)
        if binding["state"] == capture_mod.EVIDENCE_FINAL:
            self.assertEqual([m["pid"] for m in residual["alive"]], [self.helper], residual)
            self.assertEqual(entries[0].get("lifetime_binding"), "pidfs_inode", entries)
        else:
            self.assertEqual(residual["alive"], [], residual)
            self.assertEqual(entries[0].get("lifetime_binding"), "pid_tick_unverified", entries)
        self.assertEqual(pty_supervisor.proc_start_ticks(self.helper), self.helper_start, "the member was signalled")

    @DARWIN_ONLY
    def test_the_darwin_reader_states_its_microsecond_binding(self) -> None:
        """Darwin is not weakened: the helper is a positive alive member through the kernel's
        microsecond start identity re-read now (DESIGN §2.1) -- and the entry says so.  NOTE_EXIT
        pinned only the watcher's own observation while it lived."""
        session, _sentinel = self._run("f016-darwin", ack="attributed")
        residual = session.membership_residual()
        alive = [m for m in residual["alive"] if m["pid"] == self.helper]
        self.assertEqual([m.get("lifetime_binding") for m in alive], ["start_microsecond"], residual)


# ---- the reviewer's same-tick constructions, natively (PID 1 of a private namespace) ------
def _wait_next_tick() -> None:
    hz = os.sysconf("SC_CLK_TCK")
    tick = int(time.clock_gettime(time.CLOCK_BOOTTIME) * hz)
    while int(time.clock_gettime(time.CLOCK_BOOTTIME) * hz) == tick:
        pass


def _foreign_root(report: int, command: int) -> None:
    """The dispatch root: forks `old`, reaps it on command, exits on command."""
    os.setpgid(0, 0)
    r, w = os.pipe()
    _wait_next_tick()
    old = os.fork()
    if old == 0:
        os.read(r, 1); os._exit(0)
    _send(report, {"old_pid": old})
    assert _recv(command) == "reap_old"
    os.write(w, b"x")
    reaped, status = os.waitpid(old, 0)
    _send(report, {"reaped": reaped, "status": status})
    assert _recv(command) == "exit_root"
    os._exit(0)


def _external_peer(report: int) -> None:
    """A process OUTSIDE the root subtree (the driver's child) that forks a child of its own."""
    r, _w = os.pipe()
    grand = os.fork()
    if grand == 0:
        os.read(r, 1); os._exit(0)
    _send(report, {"new_pid": os.getpid(), "grand_pid": grand})
    os.read(r, 1); os._exit(0)


def _verified_subreaper() -> None:
    assert pty_supervisor._set_subreaper() == "subreaper"


def _same_tick_watcher(report: int, command: int, session, *, delay_acquisition: bool) -> None:
    """The watcher: subreaper, forks the root, drives the REAL `_Membership`.
    ``delay_acquisition``: the reviewer's admission cut -- ONLY the actual `pidfd_open` for
    `old` is delayed until the root has reaped it and the peer has been born; every metadata
    read is truthful.  Otherwise the crash cut: `old` is admitted (pidfd held), the root reaps
    it, and the watcher dies WITHOUT polling / persisting that exit."""
    try:
        _verified_subreaper()
        root_r, root_w = os.pipe(); cmd_r, cmd_w = os.pipe()
        root = os.fork()
        if root == 0:
            _foreign_root(root_w, cmd_r)
        old = _recv(root_r)["old_pid"]
        old_info = pty_supervisor._process_info(old)
        assert old_info and old_info[0] == root
        root_start = pty_supervisor.proc_start_ticks(root)
        members = pty_supervisor._Membership(
            pty_supervisor.members_path(os.fsencode(session.capture.path), session.incarnation),
            fence=session.fence, boot_id=pty_supervisor.host_boot_id(), agent_pid=root,
            agent_start_id=root_start, root_watch="subreaper")
        exchange: dict = {}
        if delay_acquisition:
            real_open = os.pidfd_open

            def delayed_open(pid, *args, **kwargs):
                if pid == old and "new" not in exchange:
                    _send(cmd_w, "reap_old")
                    assert _recv(root_r) == {"reaped": old, "status": 0}
                    _send(report, {"old_pid": old, "old_info": old_info, "root_pid": root, "root_start": root_start,
                                   "cut": "positive_parent_read_before_pidfd_open"})
                    exchange["new"] = _recv(command)
                return real_open(pid, *args, **kwargs)
            os.pidfd_open = delayed_open
            try:
                members.discover("actual_old_root_child")
            finally:
                os.pidfd_open = real_open
            members.discover("actual_foreign_peer")
        else:
            members.discover("actual_old_root_child")
            assert members._lifetimes(old, old_info[1])
            _send(cmd_w, "reap_old")
            assert _recv(root_r) == {"reaped": old, "status": 0}
            assert not members._lifetimes(old, old_info[1])[0][1].get("exited")
            _send(report, {"old_pid": old, "old_info": old_info, "root_pid": root, "root_start": root_start,
                           "cut": "watcher_dies_before_persisting_pidfd_exit"})
            exchange["new"] = _recv(command)
            # crash window: no discovery after the peer birth
        new = exchange["new"]
        _send(cmd_w, "exit_root")
        assert os.waitpid(root, 0)[1] == 0
        members.note_reaped(root)
        if delay_acquisition:
            members.discover("actual_agent_reaped")
        _send(report, {**new, "current_new": pty_supervisor._process_info(new["new_pid"]),
                       "current_grand": pty_supervisor._process_info(new["grand_pid"]),
                       "member_keys": [list(k) for k in members.members], "discovery": members.discovery})
        assert _recv(command) == "close"
        if delay_acquisition:
            members.close()
        os._exit(0)                                   # crash cut: dies holding the unpolled pidfd
    except BaseException:
        traceback.print_exc(); sys.stderr.flush(); os._exit(1)


@PRIVATE_NS
class F016SameTickForeignPeerTests(unittest.TestCase):
    """`probe_linux_same_tick_foreign_admission` / `_crash`, natively: the driver (PID 1) only
    writes ns_last_pid so the peer is BORN with the reaped child's pid at the same clock tick;
    equal ticks are asserted from /proc, never injected; cleanup targets the driver's own
    children only."""

    def _construct(self, *, delay_acquisition: bool, attempts: int = 100, reader_hook=None):
        """``reader_hook(stage, session)`` (i2) runs in the DRIVER before each residual read
        (``"live"`` / ``"recovered"``) -- the seam a source-model lock uses to change what the
        reader's inode observation returns at that moment; every other read is native."""
        from scripts.deterministic_workflow import standalone_journal as journal_mod
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        _verified_subreaper()
        for attempt in range(attempts):
            room = Room()
            session = StandaloneSession(intent={"intent_id": "peer", "run_id": "peer", "role": "WORKER"},
                                        profile=sh_profile(str(room.path)), artifact_base=room.path / "art",
                                        run_id="peer", journal=journal_mod.ExecutionJournal(room.path / "art", "peer"))
            rr, rw = os.pipe(); cr, cw = os.pipe()
            watcher = os.fork()
            if watcher == 0:
                _same_tick_watcher(rw, cr, session, delay_acquisition=delay_acquisition)
            old = _recv(rr)
            assert pty_supervisor._process_info(old["old_pid"]) is None
            _set_last_pid(old["old_pid"] - 1)
            pr, pw = os.pipe()
            peer = os.fork()
            if peer == 0:
                _external_peer(pw)
            new = _recv(pr)
            _send(cw, new)
            observed = _recv(rr)
            session.record = {"pid": old["root_pid"], "proc_start_ticks": old["root_start"],
                              "boot_id": pty_supervisor.host_boot_id(), "session_id": session.session_id,
                              "process_incarnation": session.incarnation}
            if reader_hook is not None:
                reader_hook("live", session)
            live = session.membership_residual()
            _send(cw, "close")
            self.assertEqual(os.waitpid(watcher, 0)[1], 0, "the watcher actor failed (see stderr)")
            if reader_hook is not None:
                reader_hook("recovered", session)
            recovered = session.membership_residual()
            ledger_rows = pty_supervisor.read_members(session._members_path())
            same = old["old_pid"] == new["new_pid"] and old["old_info"][1] == observed["current_new"][1]
            self.assertEqual(observed["current_new"][0], os.getpid())          # the peer is the driver's child
            _kill_own(peer); _kill_own(new["grand_pid"])
            for fd in (rr, rw, cr, cw, pr, pw):
                os.close(fd)
            room.close()
            if same:
                # the measured counterexample (pid + ticks from /proc), kept in the run log
                print(json.dumps({"same_pid_and_tick": True, "attempt": attempt, "delay_acquisition": delay_acquisition,
                                  "old": old, "new": {k: v for k, v in observed.items() if k != "discovery"},
                                  "discovery_reasons": observed["discovery"]["reasons"],
                                  "live_alive": [a["pid"] for a in live["alive"]],
                                  "recovered_alive": [a["pid"] for a in recovered["alive"]]}),
                      file=sys.stderr, flush=True)
                return {"attempt": attempt, "old": old, "new": observed, "live": live, "recovered": recovered,
                        "ledger": ledger_rows}
        self.skipTest("no actual same-tick recycled pid in %d attempts; nothing is claimed" % attempts)

    def test_a_delayed_acquisition_never_admits_the_foreign_peer_or_its_child(self) -> None:
        r = self._construct(delay_acquisition=True)
        peer, grand = r["new"]["new_pid"], r["new"]["grand_pid"]
        keys = [k[0] for k in r["new"]["member_keys"]]
        self.assertNotIn(peer, keys, r)
        self.assertNotIn(grand, keys, r)
        reasons = r["new"]["discovery"]["reasons"]
        self.assertTrue([x for x in reasons if x.startswith(IDENTITY_UNVERIFIED + ":ppid:")], reasons)
        self.assertIn(peer, r["new"]["discovery"]["pids"])
        for residual in (r["live"], r["recovered"]):
            self.assertEqual([a["pid"] for a in residual["alive"]], [], residual)
            self.assertNotIn(peer, [u.get("pid") for u in residual["unknown"] if isinstance(u, dict)])
        self.assertEqual(r["recovered"]["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)   # named, from the cut

    def test_a_reader_after_the_watcher_died_never_reports_the_foreign_peer_alive(self) -> None:
        r = self._construct(delay_acquisition=False)
        peer = r["new"]["new_pid"]
        self.assertIn(peer, [k[0] for k in r["new"]["member_keys"]])              # the OLD lifetime was a member
        for residual in (r["live"], r["recovered"]):
            self.assertNotIn(peer, [a["pid"] for a in residual["alive"]], residual)
            gone = [e for e in residual["exited_incarnations"] if e["pid"] == peer]
            unknown = [u for u in residual["unknown"] if isinstance(u, dict) and u.get("pid") == peer]
            self.assertEqual(len(gone) + len(unknown), 1, residual)
            if gone:
                self.assertEqual(gone[0]["lifetime_binding"], "pidfs_inode_mismatch", gone)
            else:
                self.assertEqual(unknown[0]["lifetime_binding"], "pid_tick_unverified", unknown)
                self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)


# =====================================================================================
# Iteration 2 (REVIEW_IMPLEMENTATION.md, run_5fcd2beac376): F-015 declared wrappers,
# F-017 parser depth, F-016 inode lifetime model
# =====================================================================================
def _grammar_profile(room: str, driver: str):
    """The INSTALLED CLI grammars (`scripts.os37_r10_real_agent.claude_profile` /
    `codex_profile` selectors: readiness, delivery, completion, result body, auth markers) on
    a python root under a `session_field` binding, so the production spawn / await_completion
    path runs the real grammar without the real binary."""
    from scripts.deterministic_workflow.standalone_profile import profile_from_mapping
    common = {"driver": driver, "binary": "sh", "supported_range": [[1, 0, 0], [99, 0, 0]],
              "bin_dirs": ["/bin"], "worktree": room, "delivery_mode": "post_ready_delivery",
              "identity_binding": "minted_echo", "identity_flag": "--session-id",
              "timeouts": {"post_exit_drain_budget_ms": 3000, "physical_exit_timeout_ms": 4000,
                           "completion_timeout_ms": 20000}}
    if driver == "claude":
        spec = {"readiness_records": [{"channel": "structured", "record_type": "system", "session_field": "session_id"}],
                "delivery_proofs": [{"channel": "structured", "record_type": "assistant"}],
                "completion_records": [{"channel": "structured", "record_type": "result", "error_field": "is_error",
                                        "success_field": "terminal_reason", "success_values": ["completed"],
                                        "binding_mode": "session_field", "binding_field": "session_id"}],
                "result_body_records": [{"channel": "structured", "record_type": "result", "body_field": "result"}],
                "auth_markers": [["error", "authentication_failed"], ["is_api_error_message", "True"],
                                 ["terminal_reason", "api_error"]]}
    else:
        spec = {"readiness_records": [{"channel": "structured", "record_type": "thread.started", "session_field": "thread_id"}],
                "delivery_proofs": [{"channel": "structured", "record_type": "item.completed", "item_type": "agent_message"},
                                    {"channel": "structured", "record_type": "turn.completed"}],
                "completion_records": [{"channel": "structured", "record_type": "turn.completed",
                                        "binding_mode": "session_field", "binding_field": "thread_id"}],
                "result_body_records": [{"channel": "structured", "record_type": "item.completed",
                                         "item_type": "agent_message", "body_field": "item.text"}],
                "auth_markers": [["type", "turn.failed"]]}
    return profile_from_mapping({**common, **spec})


CLAUDE_READY = '{"type": "system", "subtype": "init", "session_id": "SID"}'
CLAUDE_SUCCESS = '{"type": "result", "is_error": false, "session_id": "SID", "terminal_reason": "completed", "result": "early body"}'
CLAUDE_REFUSAL = '{"type": "result", "is_error": true, "session_id": "SID"}'
CODEX_READY = '{"type": "thread.started", "thread_id": "SID"}'
CODEX_SUCCESS = '{"type": "turn.completed", "thread_id": "SID", "usage": {"input_tokens": 1}}'
CODEX_REFUSAL = '{"type": "turn.failed", "error": {"message": "refusal"}}'


class F015DeclaredWrapperTests(_FramingDispatch):
    """`probe_declared_wrapper` / `probe_installed_shapes`: a parsable container whose OUTER
    type the profile declares (readiness / delivery / body) used to be exempt from the nested
    scan, so a bound refusal it held settled COMPLETED.  Every construction here runs the
    production spawn + `await_completion`; the wrapper is written by a cooperative child to
    inherited stdout (pipe-acknowledged) and the root's own serializer is unchanged."""

    def _grammar_dispatch(self, run_id: str, driver: str, *, first: str, helper: str, second: str):
        agent = self.room.path / f"root-{run_id}.py"
        agent.write_text(_FRAMING_ROOT % {"first": first, "helper": helper, "second": second})
        profile = _grammar_profile(str(self.room.path), driver)
        session, _sentinel = spawn_session(self.room, "", run_id=run_id, argv=[PYTHON, str(agent)], image=PYTHON,
                                           profile=profile)
        result = session.await_completion()
        n = session._boundary["fence"]["boundary"]["offset_n"]
        return session, result, session.capture.raw()[:n]

    def test_a_declared_readiness_wrapper_hides_no_refusal_one_line_and_two_line(self) -> None:
        """The reviewer's construction on the fixture grammar (`system` is the declared
        readiness type): one-line container (helper closes it on the same line) and the
        plain-root two-line form with the identical serializer and CRLF."""
        _s, result, prefix = self._dispatch("f015-declared-1", first=SUCCESS,
                                            helper='{"type": "system", "progress": ', second=REFUSAL + "}\n")
        self._assert_refusal_in_boundary(result, prefix)
        self.assertIn(b'{"type": "system", "progress": {"type": "result", "is_error": true', prefix)
        agent = self.room.path / "plain-declared.py"
        agent.write_text(_PLAIN_ROOT.replace("PREFIX", repr(b'{"type": "system", "progress": ')))
        _s, result, fenced = self._run("f015-declared-2", agent)
        self._assert_refusal_in_boundary(result, fenced)
        self.assertIn(b'"is_error": true, "session_id"', fenced)
        self.assertIn(b"\r\n}\r\n", fenced)                                        # CRLF-framed close

    def test_declared_wrappers_of_the_claude_grammar_hide_no_refusal(self) -> None:
        for wrapper in ("system", "assistant", "result"):
            with self.subTest(wrapper=wrapper):
                _s, result, prefix = self._grammar_dispatch(f"f015-claude-{wrapper}", "claude",
                                                            first=CLAUDE_READY + "\n" + CLAUDE_SUCCESS,
                                                            helper='{"type": "%s", "progress": ' % wrapper,
                                                            second=CLAUDE_REFUSAL + "}\n")
                self.assertIn(b'"is_error": true', prefix)
                if wrapper == "result":
                    # the container is itself a second declared completion record: R2
                    self.assertNotEqual(result["state"], "COMPLETED", result)
                    self.assertIn(result.get("lost_reason") or result["verdict"]["reason"],
                                  (capture_mod.OUTCOME_PROVENANCE_AMBIGUOUS, capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY), result)
                else:
                    self._assert_refusal_in_boundary(result, prefix)

    def test_declared_wrappers_of_the_codex_grammar_hide_no_refusal(self) -> None:
        for wrapper in ("thread.started", "item.completed"):
            with self.subTest(wrapper=wrapper):
                _s, result, prefix = self._grammar_dispatch(f"f015-codex-{wrapper}", "codex",
                                                            first=CODEX_READY + "\n" + CODEX_SUCCESS,
                                                            helper='{"type": "%s", "progress": [' % wrapper,
                                                            second=CODEX_REFUSAL + "]}\n")
                self.assertEqual(result["state"], "FAILED", result)
                self.assertEqual(result["verdict"]["reason"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, result)
                self.assertIn(b'"turn.failed"', prefix)
                self.assertEqual(result["evidence"]["refusal"]["record_type"], "turn.failed", result["evidence"]["refusal"])

    def test_a_records_own_nested_data_is_never_the_dispatchs_refusal(self) -> None:
        """Controls (GREEN before and after): a Claude `user` record's tool result with
        `is_error: true`, a Codex `item.completed` error item, and a refusal spelled inside a
        string literal of a declared wrapper are data -- the dispatch completes."""
        cases = (("claude", CLAUDE_READY + "\n" + CLAUDE_SUCCESS,
                  '{"type": "user", "message": {"content": [{"type": "tool_result", "is_error": true, "content": "x"}]}}\n'),
                 ("claude", CLAUDE_READY + "\n" + CLAUDE_SUCCESS,
                  '{"type": "assistant", "message": {"content": [{"type": "text", "text": ' + json.dumps(CLAUDE_REFUSAL) + '}]}}\n'),
                 ("codex", CODEX_READY + "\n" + CODEX_SUCCESS,
                  '{"type": "item.completed", "item": {"type": "error", "message": "bad"}}\n'),
                 ("codex", CODEX_READY + "\n" + CODEX_SUCCESS,
                  '{"type": "item.completed", "item": {"type": "agent_message", "text": ' + json.dumps(CODEX_REFUSAL) + '}}\n'))
        for i, (driver, first, helper) in enumerate(cases):
            with self.subTest(driver=driver, case=i):
                _s, result, _p = self._grammar_dispatch(f"f015-ctl-{driver}-{i}", driver, first=first, helper=helper, second="")
                self.assertEqual(result["state"], "COMPLETED", result)

    def test_the_installed_profile_factories_examine_declared_containers(self) -> None:
        """The reviewer's `probe_installed_shapes` at selector level with the REAL installed
        profile factories: declared wrappers in both grammars conceal nothing."""
        from scripts.os37_r10_real_agent import claude_profile, codex_profile
        from scripts.deterministic_workflow.standalone_drivers import driver_for
        cases = (("claude", claude_profile("/tmp"), {"type": "result", "is_error": False, "session_id": "s", "terminal_reason": "completed"},
                  {"type": "result", "is_error": True, "session_id": "s"}, ("system", "assistant")),
                 ("codex", codex_profile("/tmp", "/tmp"), {"type": "turn.completed", "thread_id": "s"},
                  {"type": "turn.failed", "error": {"message": "refusal"}}, ("thread.started", "item.completed")))
        for name, profile, success, refuse, wrappers in cases:
            d = driver_for(profile)
            for kind in wrappers:
                with self.subTest(driver=name, wrapper=kind):
                    text = json.dumps(success) + "\n" + json.dumps({"type": kind, "progress": [refuse]}) + "\n"
                    selection = d.select_completion(text, bound_value="s", sidecar_present=True)
                    self.assertEqual(selection["outcome"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, selection)
                    self.assertGreater(d.framing_scan(text)["examined"], 0)


class F017ParserDepthTests(_FramingDispatch):
    """`probe_depth_dispatch`: a PLAIN 5,000-level JSON line inside [0, N) raised an untyped
    `RecursionError` out of `structured_lines` through the production caller on the native
    interpreter; the prose-prefixed form already reached the bounded scanner.  Both are now the
    named LOST `record_scan_incomplete` (`depth_limit`); a refusal reached before the bomb
    still dominates."""

    BOMB = '{"a":' * 5000 + "1" + "}" * 5000 + "\n"

    def _settle(self, run_id: str, **kw):
        try:
            return self._dispatch(run_id, **kw)
        except RecursionError as exc:                       # the finding: untyped, not a named outcome
            self.fail(f"untyped RecursionError escaped the production caller: {exc}")

    def test_a_plain_depth_bomb_line_is_named_incomplete_never_untyped(self) -> None:
        for name, helper in (("plain", self.BOMB), ("prose", "progress: " + self.BOMB)):
            with self.subTest(case=name):
                session, result, prefix = self._settle(f"f017-{name}", first=SUCCESS, helper=helper, second="")
                self.assertGreater(prefix.count(b"{"), 5000)
                self.assertEqual((result["state"], result["lost_reason"]), ("LOST", SCAN_INCOMPLETE), result)
                rows = [r for r in session.journal.rows_for(session.intent_id)
                        if (r.get("source_vocabulary") or {}).get("provenance_outcome") == SCAN_INCOMPLETE]
                self.assertEqual((rows[-1]["source_vocabulary"].get("scan") or {}).get("reason"), "depth_limit", rows[-1])

    def test_a_refusal_reached_before_the_bomb_still_dominates(self) -> None:
        _s, result, prefix = self._settle("f017-dominance", first=SUCCESS, helper="progress: " + REFUSAL + "\n",
                                          second=self.BOMB)
        self._assert_refusal_in_boundary(result, prefix)

    def test_every_line_parser_is_total_over_a_depth_bomb(self) -> None:
        bomb = self.BOMB.strip()
        # whether the interpreter's JSON parser follows 5,000 levels (Linux py3.12 does) or
        # raises RecursionError (native py3.11 does), the parser is TOTAL and the line ends in
        # the same named depth outcome: unparsable -> embedded scan -> `depth_limit`, or parsed
        # -> nested walk -> `depth_limit`
        parsed = capture_mod.parse_record_line(bomb)
        self.assertIn(type(parsed), (type(None), dict))
        self.assertEqual([type(p) for p, _ in capture_mod.structured_lines(bomb + "\n")], [type(parsed)])
        if parsed is None:
            self.assertEqual(capture_mod.unparsable_runs(bomb + "\n"), [bomb])
        scan = capture_mod.embedded_scan(bomb)
        self.assertEqual((scan["complete"], scan["reason"]), (False, "depth_limit"), scan)
        from scripts.deterministic_workflow.standalone_drivers import driver_for
        d = driver_for(sh_profile("/tmp", binding_mode="session_field", binding_field="session_id"))
        text = json.dumps({"type": "result", "is_error": False, "session_id": "s"}) + "\n" + bomb + "\n"
        self.assertEqual(d.select_completion(text, bound_value="s")["outcome"], SCAN_INCOMPLETE)
        # the readiness reader (the pump-time consumer of the same parser) is total too
        readiness = d.readiness_evidence(text, minted_session_id="s", liveness=None)
        self.assertIsInstance(readiness, dict)
        self.assertEqual(readiness["refusals"], ())


_REAL_UNAME = os.uname()


def _uname_32bit():
    """A `uname` answer for a 32-bit kernel (the source-modelled recyclable branch); the
    release is kept so ONLY the width differs."""
    real = _REAL_UNAME
    return os.uname_result((real.sysname, real.nodename, real.release, real.version, "armv7l"))


@LINUX_ONLY
class F016InodeModelTests(_MembershipCase):
    """The pidfd inode is a lifetime binding ONLY under the proven non-recyclable model (a
    64-bit kernel at or after 6.9: the monotonic struct-pid axis of the inspected v6.12 /
    v6.16 `fs/pidfs.c`); a 32-bit kernel's inode (IDA-allocated and freed on eviction in
    v6.12) is recyclable and must never promote a lifetime alive."""

    def test_the_model_is_proven_only_for_a_64_bit_kernel_at_or_after_6_9(self) -> None:
        real = os.uname()
        proven = real.machine in pty_supervisor._SIXTY_FOUR_BIT_MACHINES and pty_supervisor._kernel_release_tuple(real.release) >= (6, 9)
        self.assertEqual(pty_supervisor.pidfs_lifetime_model(), pty_supervisor.PIDFS_MODEL_STRUCT_PID_64 if proven else "")
        with patch.object(os, "uname", _uname_32bit):
            self.assertEqual(pty_supervisor.pidfs_lifetime_model(), "")
        old = os.uname_result((real.sysname, real.nodename, "6.8.0-1018-azure", real.version, real.machine))
        with patch.object(os, "uname", lambda: old):
            self.assertEqual(pty_supervisor.pidfs_lifetime_model(), "")
        self.assertEqual(pty_supervisor._kernel_release_tuple("6.12.76-linuxkit"), (6, 12))
        self.assertEqual(pty_supervisor._kernel_release_tuple("garbage"), (0, 0))

    def test_an_unproven_model_records_no_binding_and_reads_none(self) -> None:
        child, _w = self._held_child()
        with patch.object(os, "uname", _uname_32bit):
            m = self._membership(os.getpid())
            with patch.object(m, "_list_candidates", return_value=([child], "")):
                self.assertEqual(m.discover("model"), 1, m.discovery)
            record = m.members[next(k for k in m.members if k[0] == child)]
            self.assertEqual(record.get("fixed_object"), "pidfd")                  # admission still holds the pidfd
            self.assertNotIn("fixed_object_id", record, record)
            self.assertNotIn("fixed_object_model", record, record)
            binding = pty_supervisor.pidfd_binding(child)
            self.assertEqual((binding["state"], binding["model"]), ("unavailable", ""), binding)

    def test_a_recorded_binding_under_an_unproven_or_different_model_is_unknown(self) -> None:
        from scripts.deterministic_workflow import standalone_journal as journal_mod
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        session = StandaloneSession(intent={"intent_id": "i-model", "run_id": "model", "role": "WORKER"},
                                    profile=sh_profile(str(self.room.path)), artifact_base=self.room.path / "art",
                                    run_id="model", journal=journal_mod.ExecutionJournal(self.room.path / "art", "model"))
        os.makedirs(os.path.dirname(str(session.capture.path)), exist_ok=True)
        live, _w = self._held_child()
        start = pty_supervisor.proc_start_ticks(live)
        binding = pty_supervisor.pidfd_binding(live)
        if binding["state"] != capture_mod.EVIDENCE_FINAL:
            self.skipTest(f"no proven inode model on this kernel ({binding})")
        ident = pty_supervisor._member_identity(live, start, pty_supervisor.host_boot_id(), session.fence)
        cases = (("no_model", {"fixed_object_id": binding["fixed_object_id"]}, "binding_model:unproven", None),
                 ("reader_32bit", {"fixed_object_id": binding["fixed_object_id"], "fixed_object_model": binding["model"]},
                  f"binding_model:mismatch:{binding['model']}!=unproven", _uname_32bit))
        for name, extra, detail, uname in cases:
            with self.subTest(case=name):
                path = session._members_path()
                with contextlib.suppress(FileNotFoundError):
                    os.remove(os.fsdecode(path))
                pty_supervisor._append_member(path, {"schema": pty_supervisor.MEMBER_SCHEMA, "event": "observed", "identity": ident,
                                                     "role": "descendant", "observed_via": "t", "pgid": 0, "lifetime": 1,
                                                     "fixed_object": "pidfd", **extra})
                Path(os.fsdecode(path) + ".state.json").write_text(json.dumps({
                    "schema": "os48.member.v1.state", "appended": 1, "failed": 0, "members": 1,
                    "discovery": {"passes": 1, "listing_unreadable": 0, "listing_unstable": 0, "candidates_unreadable": 0,
                                  "forks_coalesced": 0, "watch_gaps": 0, "parents_unreadable": 0, "unobservable": 0,
                                  "root_watch": "subreaper", "reasons": [], "pids": []}}))
                ctx = patch.object(os, "uname", uname) if uname else contextlib.nullcontext()
                with ctx:
                    residual = session.membership_residual()
                self.assertEqual([e for e in residual["alive"] if e["pid"] == live], [], residual)
                unknown = [u for u in residual["unknown"] if isinstance(u, dict) and u.get("pid") == live]
                self.assertEqual([(u["lifetime_binding"], u["binding_detail"]) for u in unknown],
                                 [("pid_tick_unverified", detail)], residual)
                self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)


@PRIVATE_NS
class F016RecyclableInodeModelTests(F016SameTickForeignPeerTests):
    # the inherited 64-bit locks are NOT re-run here (they belong to the class above)
    test_a_delayed_acquisition_never_admits_the_foreign_peer_or_its_child = None
    test_a_reader_after_the_watcher_died_never_reports_the_foreign_peer_alive = None

    """`probe_recyclable_inode_model` (source-faithful port): the reviewer modelled the Linux
    v6.12 32-bit pidfs branch -- the inode number is freed on eviction, so after the watcher
    dies (its held handle closed) a DIFFERENT birth may receive the recorded number.  Only the
    inode observation is modelled; the kernel width is reported as 32-bit (the branch that
    model belongs to); births, ticks, parentage, pidfds and polls are native.  Under the fix
    the reader never promotes the recycled equality: the lifetime is `unknown` by name."""

    def _assert_never_alive(self, r, detail_prefix: str) -> None:
        peer = r["new"]["new_pid"]
        self.assertIn(peer, [k[0] for k in r["new"]["member_keys"]])              # the OLD lifetime was a member
        for stage in ("live", "recovered"):                                        # the finding: alive after death
            self.assertNotIn(peer, [a["pid"] for a in r[stage]["alive"]], (stage, r[stage]))
        for stage in ("live", "recovered"):                                        # the fix: unknown by name, both
            residual = r[stage]
            unknown = [u for u in residual["unknown"] if isinstance(u, dict) and u.get("pid") == peer]
            self.assertEqual(len(unknown), 1, residual)
            self.assertEqual(unknown[0]["lifetime_binding"], "pid_tick_unverified", unknown)
            self.assertTrue(unknown[0]["binding_detail"].startswith(detail_prefix), unknown)
            self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)

    def test_a_32_bit_model_records_no_binding_and_the_reader_stays_unknown(self) -> None:
        """Watcher AND reader on the modelled 32-bit branch (uname width), the inode
        observation replaced by the reviewer's recycling model exactly (every non-self pidfd
        reads 400; the reader reads 401 while the watcher still holds 400 and 400 again once
        the watcher's death freed it): no binding is recorded, none is read, the peer is never
        alive.  At i1 the same construction reported the peer alive after the watcher died."""
        value = {"ino": 400}
        real_inode = pty_supervisor._pidfd_inode

        def recycled(fd: int) -> int:
            return real_inode(fd) if _fd_pid(fd) == os.getpid() else value["ino"]

        def hook(stage: str, session) -> None:
            value["ino"] = 401 if stage == "live" else 400
        with patch.object(os, "uname", _uname_32bit), patch.object(pty_supervisor, "_pidfd_inode", recycled):
            r = self._construct(delay_acquisition=False, reader_hook=hook)
        self._assert_never_alive(r, "no_recorded_fixed_object_id")
        self.assertTrue(all("fixed_object_id" not in m for m in r["ledger"]), r["ledger"])

    def test_a_binding_recorded_under_the_proven_model_is_not_read_under_another(self) -> None:
        """The watcher recorded the binding under this kernel's proven 64-bit model; the reader
        runs where the width reads 32-bit and the inode observation is the recycled number
        (equal to the recorded one after the watcher died): the models differ, so the equality
        is refused by name -- never alive."""
        real_inode = pty_supervisor._pidfd_inode
        recorded = {"ino": 0, "offset": 1}

        def recycled(fd: int) -> int:
            return real_inode(fd) if _fd_pid(fd) == os.getpid() else recorded["ino"] + recorded["offset"]

        def hook(stage: str, session) -> None:
            row = next(m for m in pty_supervisor.read_members(session._members_path()) if m.get("role") == "descendant")
            self.assertTrue(int(row.get("fixed_object_id") or 0), row)             # recorded under the proven model
            self.assertEqual(row.get("fixed_object_model"), pty_supervisor.PIDFS_MODEL_STRUCT_PID_64, row)
            recorded["ino"], recorded["offset"] = int(row["fixed_object_id"]), (1 if stage == "live" else 0)
            if not recorded.get("patched"):
                recorded["patched"] = True
                for ptch in (patch.object(os, "uname", _uname_32bit), patch.object(pty_supervisor, "_pidfd_inode", recycled)):
                    ptch.start()
                    self.addCleanup(ptch.stop)
        r = self._construct(delay_acquisition=False, reader_hook=hook)
        self._assert_never_alive(r, "binding_model:mismatch:")


def _fd_pid(fd: int) -> int:
    text = Path(f"/proc/self/fdinfo/{fd}").read_text()
    return int(next(line.split(":")[1] for line in text.splitlines() if line.startswith("Pid:")))


# =====================================================================================
# Iteration 3 (REVIEW_IMPLEMENTATION_iteration2.md): F-015 integer-conversion limit,
# F-017 parser resource failure, F-016 boot join
# =====================================================================================
#: The reviewer's numeric-counter root (`probe_integer_parser_limit`): the WRITER lifts its
#: own conversion limit to serialise an arbitrary-precision counter; the reader is untouched.
_INTEGER_ROOT = """import os,sys,json
sys.set_int_max_str_digits(0)
sid=os.environ['OS48_TEST_SID']
first=%(first)r
if first: os.write(1,(first.replace('SID',sid)+'\\n').encode())
value=10**(%(digits)d-1)
if %(string)r: value=str(value)
packet=json.loads(%(packet)r.replace('SID',sid)); packet['counter']=value
os.write(1,(%(prefix)r+json.dumps(packet)+%(suffix)r+'\\n').encode())
os._exit(0)
"""


class _IntegerDispatch(_FramingDispatch):
    def _integer_dispatch(self, run_id: str, *, first: str, packet: str, digits: int, string: bool = False,
                          prefix: str = "", suffix: str = "", driver: str = ""):
        agent = self.room.path / f"root-{run_id}.py"
        agent.write_text(_INTEGER_ROOT % {"first": first, "digits": digits, "string": string, "packet": packet,
                                          "prefix": prefix, "suffix": suffix})
        kw = {"profile": _grammar_profile(str(self.room.path), driver)} if driver else \
             {"binding_mode": "session_field", "binding_field": "session_id"}
        session, _sentinel = spawn_session(self.room, "", run_id=run_id, argv=[PYTHON, str(agent)], image=PYTHON, **kw)
        result = session.await_completion()
        n = session._boundary["fence"]["boundary"]["offset_n"]
        return session, result, session.capture.raw()[:n]


class F015IntegerLimitTests(_IntegerDispatch):
    """`probe_integer_parser_limit` / `probe_integer_installed_grammar_callers`: a valid bound
    refusal whose integer token exceeds the interpreter's conversion limit (4,300 digits) was
    read as invalid prose by both parsers; the scan reported complete and the early success
    won.  The reader now parses integers under its own digit budget (an over-budget token is
    kept as an unconverted digit string) so the object is examined WHOLE and the refusal is
    recognised; a conversion failure the parser still hits is a named incomplete scan."""

    LIMIT = sys.get_int_max_str_digits()

    def test_an_over_limit_integer_never_hides_a_bound_refusal(self) -> None:
        cases = (("at_limit", self.LIMIT), ("over_limit", self.LIMIT + 1), ("5000", max(5000, self.LIMIT + 1)))
        for name, digits in cases:
            with self.subTest(case=name, digits=digits):
                _s, result, prefix = self._integer_dispatch(f"f015-int-{name}", first=SUCCESS, packet=REFUSAL, digits=digits)
                self.assertIn(b'"is_error": true', prefix)
                self._assert_refusal_in_boundary(result, prefix)

    def test_over_limit_progress_and_string_data_controls(self) -> None:
        """A valid over-limit numeric PROGRESS object after the success is examined whole and
        is no refusal -> COMPLETED; the same digits as string data on a refusal still refuse."""
        digits = max(5000, self.LIMIT + 1)
        _s, result, prefix = self._integer_dispatch("f015-int-progress", first=SUCCESS,
                                                    packet='{"type": "system", "session_id": "SID"}', digits=digits)
        self.assertEqual(result["state"], "COMPLETED", result)
        self.assertIn(("1" + "0" * (digits - 1)).encode(), prefix)                # the token is inside [0, N)
        _s, result, prefix = self._integer_dispatch("f015-int-string", first=SUCCESS, packet=REFUSAL, digits=digits, string=True)
        self._assert_refusal_in_boundary(result, prefix)

    def test_prose_prefixed_and_split_framing_reach_the_same_bounded_parser(self) -> None:
        digits = max(5000, self.LIMIT + 1)
        _s, result, prefix = self._integer_dispatch("f015-int-prose", first=SUCCESS, packet=REFUSAL, digits=digits, prefix="progress: ")
        self._assert_refusal_in_boundary(result, prefix)
        _s, result, prefix = self._integer_dispatch("f015-int-wrap", first=SUCCESS, packet=REFUSAL, digits=digits,
                                                    prefix='{"type": "system", "progress": ', suffix="}")
        self._assert_refusal_in_boundary(result, prefix)

    def test_both_installed_grammars_refuse_an_over_limit_refusal(self) -> None:
        digits = max(5000, self.LIMIT + 1)
        for driver, first, packet, marker in (("claude", CLAUDE_READY + "\n" + CLAUDE_SUCCESS, CLAUDE_REFUSAL, b'"is_error": true'),
                                              ("codex", CODEX_READY + "\n" + CODEX_SUCCESS,
                                               '{"type": "turn.failed", "thread_id": "SID", "error": {"message": "refusal"}}', b'"turn.failed"')):
            with self.subTest(driver=driver):
                _s, result, prefix = self._integer_dispatch(f"f015-int-{driver}", first=first, packet=packet, digits=digits, driver=driver)
                self.assertIn(marker, prefix)
                self.assertEqual((result["state"], result["verdict"]["reason"]), ("FAILED", capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY), result)

    def test_the_installed_selector_factories_refuse_over_limit_refusals(self) -> None:
        from scripts.os37_r10_real_agent import claude_profile, codex_profile
        from scripts.deterministic_workflow.standalone_drivers import driver_for
        token = "1" + "0" * max(5000, self.LIMIT)
        for name, profile, success, refuse in (
                ("claude", claude_profile("/tmp"), {"type": "result", "is_error": False, "session_id": "s", "terminal_reason": "completed", "result": "body"},
                 {"type": "result", "is_error": True, "session_id": "s", "counter": "INTEGER_TOKEN"}),
                ("codex", codex_profile("/tmp", "/tmp"), {"type": "turn.completed", "thread_id": "s"},
                 {"type": "turn.failed", "thread_id": "s", "counter": "INTEGER_TOKEN"})):
            with self.subTest(driver=name):
                literal = json.dumps(refuse).replace('"INTEGER_TOKEN"', token)
                text = json.dumps(success) + "\n" + literal + "\n"
                selection = driver_for(profile).select_completion(text, bound_value="s", sidecar_present=True)
                self.assertEqual(selection["outcome"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, selection)

    def test_the_bounded_parser_keeps_over_budget_integers_unconverted_and_names_conversion_failures(self) -> None:
        token = "1" + "0" * (capture_mod.INTEGER_DIGIT_BUDGET + 5)
        record = capture_mod.parse_record_line('{"type": "result", "is_error": true, "counter": %s}' % token)
        self.assertIsInstance(record["counter"], capture_mod.UnconvertedInteger)
        self.assertEqual(record["counter"], token)
        self.assertIs(record["is_error"], True)
        self.assertEqual(capture_mod.parse_record_line('{"n": 42}')["n"], 42)                   # ordinary integers convert
        self.assertEqual(capture_mod.line_parse_failures('{"counter": %s}\n' % token), [])
        # a conversion the parser still cannot make is a NAMED failure, never invalid prose
        with patch.object(capture_mod, "_bounded_int", lambda tok: (_ for _ in ()).throw(ValueError("modelled conversion limit"))):
            self.assertIsNone(capture_mod.parse_record_line('{"n": 1}'))
            self.assertEqual(capture_mod.line_parse_failures('{"n": 1}\n'), [{"index": 0, "reason": "conversion_limit"}])
            scan = capture_mod.embedded_scan('progress: {"n": 1}')
            self.assertEqual((scan["complete"], scan["reason"]), (False, "conversion_limit"), scan)
        # a syntax rejection stays prose (complete, no candidate)
        self.assertEqual(capture_mod.line_parse_failures('{"broken": \n'), [])
        self.assertTrue(capture_mod.embedded_scan('{"broken": ')["complete"])


class F017ParserResourceTests(_FramingDispatch):
    """`probe_native_memoryerror`: under real address-space pressure the C JSON scanner raised
    `MemoryError` from `parse_record_line` through the production caller.  The seam here is
    deterministic and bounded -- `json.loads` raises `MemoryError` for the ONE marked line --
    never host memory stress.  Every parser reached returns a named typed result."""

    MARK = "resource-marker-7c1d"

    @contextlib.contextmanager
    def _memory_error_for_marked_line(self):
        real = json.loads

        def failing(text, *args, **kwargs):
            if isinstance(text, str) and self.MARK in text:
                raise MemoryError("modelled JSON allocation failure")
            return real(text, *args, **kwargs)
        with patch.object(json, "loads", failing):
            yield

    def _settle(self, run_id: str, **kw):
        with self._memory_error_for_marked_line():
            try:
                return self._dispatch(run_id, **kw)
            except MemoryError as exc:
                self.fail(f"MemoryError escaped the production caller: {exc}")

    def test_a_parser_allocation_failure_is_a_named_incomplete_scan_never_an_escape(self) -> None:
        refusal = REFUSAL[:-1] + ', "note": "%s"}' % self.MARK
        for name, helper in (("plain", refusal + "\n"), ("prose", "progress: " + refusal + "\n"),
                             ("two-line", '{"progress": ' + refusal + "\n}\n")):
            with self.subTest(case=name):
                session, result, prefix = self._settle(f"f017-mem-{name}", first=SUCCESS, helper=helper, second="")
                self.assertIn(self.MARK.encode(), prefix)
                self.assertNotEqual(result["state"], "COMPLETED", result)
                self.assertEqual((result["state"], result["lost_reason"]), ("LOST", SCAN_INCOMPLETE), result)
                rows = [r for r in session.journal.rows_for(session.intent_id)
                        if (r.get("source_vocabulary") or {}).get("provenance_outcome") == SCAN_INCOMPLETE]
                self.assertEqual((rows[-1]["source_vocabulary"].get("scan") or {}).get("reason"), "resource_limit", rows[-1])

    def test_a_refusal_reached_before_the_failure_still_dominates_and_normal_settlement_is_retained(self) -> None:
        marked = '{"type": "system", "note": "%s"}\n' % self.MARK
        _s, result, prefix = self._settle("f017-mem-dominance", first=SUCCESS, helper="progress: " + REFUSAL + "\n", second=marked)
        self._assert_refusal_in_boundary(result, prefix)
        _s, result, _p = self._settle("f017-mem-control", first=SUCCESS, helper='{"type": "system", "note": "unmarked"}\n', second="")
        self.assertEqual(result["state"], "COMPLETED", result)

    def test_every_parser_classifies_the_resource_failure_by_name(self) -> None:
        line = '{"type": "result", "is_error": true, "note": "%s"}' % self.MARK
        with self._memory_error_for_marked_line():
            with self.assertRaises(capture_mod.ParseFailure) as caught:
                capture_mod.parse_json(line)
            self.assertEqual(caught.exception.reason, "resource_limit")
            self.assertIsNone(capture_mod.parse_record_line(line))
            self.assertEqual(capture_mod.line_parse_failures(line + "\n"), [{"index": 0, "reason": "resource_limit"}])
            scan = capture_mod.embedded_scan(line)
            self.assertEqual((scan["complete"], scan["reason"], scan["objects"]), (False, "resource_limit", []), scan)
            from scripts.deterministic_workflow.standalone_drivers import driver_for
            d = driver_for(sh_profile("/tmp", binding_mode="session_field", binding_field="session_id"))
            text = json.dumps({"type": "result", "is_error": False, "session_id": "s"}) + "\n" + line + "\n"
            selection = d.select_completion(text, bound_value="s")
            self.assertEqual((selection["outcome"], selection["scan"]["reason"]), (SCAN_INCOMPLETE, "resource_limit"), selection)
            readiness = d.readiness_evidence(text, minted_session_id="s", liveness=None)   # the pump-time reader
            self.assertIsInstance(readiness, dict)
            # the fallback parsers reached by that result never raise and never read the failed
            # line as a record: only the success is a structured record, and the selector above
            # has already refused to settle on it
            self.assertEqual([r.get("is_error") for r in d.structured_records(text)], [False])
            self.assertEqual(d.completion_record(text).get("is_error"), False)


class F016BootJoinTests(unittest.TestCase):
    """`probe_recovery_boot_matrix` / `probe_cross_boot_*`: the reader accepted equal pid /
    start / inode without comparing the ledger's `identity.boot_id` to its own boot id, and
    every boot-scoped binding (start tick, the per-boot pidfs inode counter, darwin's re-read
    start time) restarts on another boot.  The BOOT JOIN: alive only when a non-empty recorded
    boot id equals a non-empty current boot id; missing / unreadable / different -> `unknown`
    `boot_unjoined` by name, never alive.  The boot source is the only seam; the child, its
    pidfd and inode are native."""

    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)
        r, w = os.pipe()
        self.child = os.fork()
        if self.child == 0:
            os.close(w); os.read(r, 1); os._exit(0)
        self.addCleanup(_quiet, lambda: os.write(w, b"x"), lambda: os.waitpid(self.child, 0))
        self.boot = pty_supervisor.host_boot_id()
        self.assertTrue(self.boot, "this host's boot id is unreadable: the matrix cannot be built")

    def _residual(self, name: str, *, recorded_boot: str, observed_boot: str):
        from scripts.deterministic_workflow import standalone_journal as journal_mod
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        session = StandaloneSession(intent={"intent_id": name, "run_id": name, "role": "WORKER"},
                                    profile=sh_profile(str(self.room.path)), artifact_base=self.room.path / "art",
                                    run_id=name, journal=journal_mod.ExecutionJournal(self.room.path / "art", name))
        session.record = {"pid": os.getpid()}
        os.makedirs(os.path.dirname(str(session.capture.path)), exist_ok=True)
        m = pty_supervisor._Membership(pty_supervisor.members_path(os.fsencode(session.capture.path), session.incarnation),
                                       fence=session.fence, boot_id=recorded_boot, agent_pid=os.getpid(),
                                       agent_start_id=pty_supervisor.proc_start_ticks(os.getpid()), root_watch="subreaper")
        self.addCleanup(m.close)
        self.assertTrue(m._add(self.child, pty_supervisor.proc_start_ticks(self.child), pty_supervisor.MEMBER_ROLE_DESCENDANT,
                               "native_own_child", ppid=os.getpid()))
        with patch.object(pty_supervisor, "host_boot_id", lambda: observed_boot):
            residual = session.membership_residual()
        return residual

    def _entry(self, residual, bucket):
        return [e for e in residual[bucket] if isinstance(e, dict) and e.get("pid") == self.child]

    def test_a_matching_boot_keeps_the_positive_path(self) -> None:
        residual = self._residual("boot-match", recorded_boot=self.boot, observed_boot=self.boot)
        alive = self._entry(residual, "alive")
        if sys.platform == "linux" and pty_supervisor.pidfd_binding(self.child)["state"] != capture_mod.EVIDENCE_FINAL:
            self.skipTest("no proven inode model on this kernel: the within-boot positive path is not available here")
        self.assertEqual(len(alive), 1, residual)
        self.assertTrue(alive[0].get("boot_joined"), alive)
        self.assertIn(alive[0]["lifetime_binding"], ("pidfs_inode", "start_microsecond"))

    def test_missing_unreadable_or_different_boot_is_unknown_never_alive(self) -> None:
        cases = (("recorded_missing", "", self.boot, "boot_id:unrecorded"),
                 ("current_unreadable", self.boot, "", "boot_id:unreadable"),
                 ("different_boot", self.boot, "model-new-boot", "boot_id:mismatch"))
        for name, recorded, observed, detail in cases:
            with self.subTest(case=name):
                residual = self._residual(f"boot-{name}", recorded_boot=recorded, observed_boot=observed)
                self.assertEqual(self._entry(residual, "alive"), [], residual)
                unknown = self._entry(residual, "unknown")
                self.assertEqual([(u["lifetime_binding"], u["binding_detail"]) for u in unknown], [("boot_unjoined", detail)], residual)
                self.assertEqual(residual["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN, residual)


@PRIVATE_NS
class F016BootCounterResetTests(F016SameTickForeignPeerTests):
    """`probe_cross_boot_after_death_model` (source-faithful): the 64-bit pidfs inode counter
    restarts on every boot, so after a watcher's death a peer born under a LATER boot with the
    same pid / tick / counter value matched the recorded inode and was reported alive.  Only
    the boot source and the inode observation are modelled; births, ticks, parentage, pidfds
    and polls are native.  Under the boot join the peer is `unknown` by name."""
    test_a_delayed_acquisition_never_admits_the_foreign_peer_or_its_child = None
    test_a_reader_after_the_watcher_died_never_reports_the_foreign_peer_alive = None

    def test_a_counter_reset_on_another_boot_never_reports_the_foreign_peer_alive(self) -> None:
        real_inode = pty_supervisor._pidfd_inode
        value = {"ino": 400}

        def modelled_inode(fd: int) -> int:
            return real_inode(fd) if _fd_pid(fd) == os.getpid() else value["ino"]

        def hook(stage: str, session) -> None:
            value["ino"] = 401 if stage == "live" else 400                        # equal counter on the "new boot"
            if not value.get("rebooted"):
                value["rebooted"] = True
                ptch = patch.object(pty_supervisor, "host_boot_id", lambda: "model-boot-after")
                ptch.start(); self.addCleanup(ptch.stop)
        with patch.object(pty_supervisor, "host_boot_id", lambda: "model-boot-before"), \
                patch.object(pty_supervisor, "_pidfd_inode", modelled_inode):
            r = self._construct(delay_acquisition=False, reader_hook=hook)
        peer = r["new"]["new_pid"]
        self.assertIn(peer, [k[0] for k in r["new"]["member_keys"]])
        self.assertEqual({row["identity"]["boot_id"] for row in r["ledger"] if row.get("event") == "observed"}, {"model-boot-before"})
        for stage in ("live", "recovered"):
            self.assertNotIn(peer, [a["pid"] for a in r[stage]["alive"]], (stage, r[stage]))
        unknown = [u for u in r["recovered"]["unknown"] if isinstance(u, dict) and u.get("pid") == peer]
        self.assertEqual([(u["lifetime_binding"], u["binding_detail"]) for u in unknown], [("boot_unjoined", "boot_id:mismatch")], r["recovered"])
        self.assertEqual(r["recovered"]["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)


# =====================================================================================
# F-017 (run_7859f202457c; REVIEW_IMPLEMENTATION_iteration3 of run_5fcd2beac376) -- a SELECTED
# refusal is never overwritten by a reader failure that comes AFTER the selection
# =====================================================================================
class F017SelectedRefusalDominanceTests(_FramingDispatch):
    """The reviewer's `probe_reached_refusal_contract` / `probe_reached_refusal_witness`, ported
    as the required assertion: the production spawn + `await_completion` over a real cooperative
    PTY root that writes a BOUND success and then a BOUND `is_error: true` refusal, exits 0; the
    actual production `select_completion` is wrapped (never replaced) to record when it returns
    a positive `refusal_in_boundary`; from that moment on, the NEXT read of the fenced range
    raises a deterministic `MemoryError` (an allocation seam, never host memory stress).

    At 709cea0 `completion()` re-read `[baseline, N)` after the selection (`_authoritative_text`),
    and the failure of THAT read replaced the selected refusal with `record_scan_incomplete`:
    `await_completion` returned LOST with no verdict.  DESIGN §1.4 R1 / ORIGINAL_REQUEST §2:
    a refusal positively established over the verified boundary dominates every later parser /
    reader failure -- FAILED `refusal_in_boundary`, the refusal provenance retained.  The fix
    removes the redundant read (nothing consumed its result); the seams below are armed on the
    production session and stay armed through the whole wait, so the lock holds whether the
    read is absent (now) or ever reintroduced as a diagnostic-only read."""

    #: the reviewer's root: two bound records in stream order, success first, refusal second
    ROOT = """import os,json
sid=os.environ['OS48_TEST_SID']
for is_error in (False,True):
 os.write(1,(json.dumps(dict(type='result',is_error=is_error,session_id=sid,result='body'))+'\\n').encode())
"""
    #: the completion counterpart: one bound success, nothing else
    SUCCESS_ROOT = """import os,json
sid=os.environ['OS48_TEST_SID']
os.write(1,(json.dumps(dict(type='result',is_error=False,session_id=sid,result='body'))+'\\n').encode())
"""

    def _spawn(self, run_id: str, root: str):
        agent = self.room.path / f"root-{run_id}.py"
        agent.write_text(root)
        session, _sentinel = spawn_session(self.room, "", run_id=run_id, argv=[PYTHON, str(agent)], image=PYTHON,
                                           binding_mode="session_field", binding_field="session_id")
        return session

    @contextlib.contextmanager
    def _fault_after_selection(self, session, *, reached_when, seam: str):
        """Wrap the REAL selector to record every positive selection (`reached_when(selection)`);
        once one has been reached, the named reader (`authoritative_text` = the reviewer's
        exact seam `_authoritative_text`; `capture_raw` = ANY raw read of the capture) raises
        `MemoryError` on every later call.  Yields ``{"reached": [...], "faults": [...]}``."""
        state = {"reached": [], "faults": []}
        real_select = session.driver.select_completion
        real_text = session._authoritative_text
        real_raw = session.capture.raw

        def select(*args, **kwargs):
            selection = real_select(*args, **kwargs)
            if reached_when(selection):
                state["reached"].append(selection)
            return selection

        def failing_text():
            if state["reached"]:
                state["faults"].append("_authoritative_text after a positive selection")
                raise MemoryError("deterministic post-selection reader allocation seam")
            return real_text()

        def failing_raw(cursor: int = 0):
            if state["reached"]:
                state["faults"].append(f"capture.raw({cursor}) after a positive selection")
                raise MemoryError("deterministic post-selection capture read seam")
            return real_raw(cursor)

        patches = [patch.object(session.driver, "select_completion", select)]
        if seam == "authoritative_text":
            patches.append(patch.object(session, "_authoritative_text", failing_text))
        elif seam == "capture_raw":
            patches.append(patch.object(session.capture, "raw", failing_raw))
        else:
            assert seam == "none", seam
        with contextlib.ExitStack() as stack:
            for ptch in patches:
                stack.enter_context(ptch)
            yield state

    def _verified_prefix(self, session) -> bytes:
        """`[0, N)` re-read AFTER the seams are gone, joined to the fence's own sha256."""
        fence = session._boundary["fence"]
        n = fence["boundary"]["offset_n"]
        prefix = session.capture.raw()[:n]
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), fence["boundary"]["sha256_prefix"])
        return prefix

    def _await_with_seam(self, run_id: str, root: str, *, seam: str, reached_when):
        session = self._spawn(run_id, root)
        with self._fault_after_selection(session, reached_when=reached_when, seam=seam) as state:
            try:
                result = session.await_completion()
            except MemoryError as exc:
                self.fail(f"MemoryError escaped the production caller: {exc}")
        return session, result, state, self._verified_prefix(session)

    @staticmethod
    def _is_refusal(selection) -> bool:
        return selection.get("refusal") is not None

    @staticmethod
    def _is_bound_completion(selection) -> bool:
        return selection.get("record") is not None and selection.get("refusal") is None

    def _assert_selected_refusal_retained(self, result, state, prefix) -> None:
        self.assertTrue(state["reached"], "the production selector never reached the refusal")
        self.assertEqual(state["reached"][-1]["outcome"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, state["reached"][-1])
        self.assertEqual(state["reached"][-1]["refusal"]["source"], "error_field", state["reached"][-1])
        self.assertTrue(any(json.loads(line).get("is_error") is True
                            for line in prefix.decode().splitlines() if line.strip()),
                        "the refusal is not inside [0, N)")
        # the reviewer's required disposition, verbatim: FAILED / refusal_in_boundary
        self.assertEqual(result["state"], "FAILED", result)
        self.assertEqual((result.get("verdict") or {}).get("reason"), capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, result)
        self.assertEqual(result.get("lost_reason"), "", result)
        # ... and the refusal PROVENANCE is retained on the evidence, not merely the state
        evidence = result["evidence"]
        self.assertEqual(evidence["provenance_outcome"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, evidence)
        self.assertEqual((evidence.get("refusal") or {}).get("source"), "error_field", evidence)
        self.assertNotEqual((evidence.get("source_vocabulary") or {}).get("provenance_outcome"), SCAN_INCOMPLETE, evidence)

    def test_a_selected_refusal_survives_a_memory_error_in_the_read_that_follows_the_selection(self) -> None:
        """The reviewer's exact ordering: first read -> a valid bound refusal is selected; the
        next `_authoritative_text` -> `MemoryError`; final state FAILED `refusal_in_boundary`."""
        _s, result, state, prefix = self._await_with_seam(
            "f017-selected-refusal-text", self.ROOT, seam="authoritative_text", reached_when=self._is_refusal)
        self._assert_selected_refusal_retained(result, state, prefix)

    def test_a_selected_refusal_survives_a_memory_error_in_any_later_capture_read(self) -> None:
        """Stronger than the reviewer's seam: EVERY raw read of the capture after the selection
        fails, whatever reader would issue it -- the selection still settles the dispatch."""
        _s, result, state, prefix = self._await_with_seam(
            "f017-selected-refusal-raw", self.ROOT, seam="capture_raw", reached_when=self._is_refusal)
        self._assert_selected_refusal_retained(result, state, prefix)

    def test_the_normal_control_settles_the_same_refusal(self) -> None:
        """No fault at all (the reviewer's `REFUSAL_CONTROL`): the same root, the same verdict."""
        _s, result, state, prefix = self._await_with_seam(
            "f017-selected-refusal-control", self.ROOT, seam="none", reached_when=self._is_refusal)
        self._assert_selected_refusal_retained(result, state, prefix)
        self.assertEqual(state["faults"], [])

    def test_a_selected_completion_survives_a_memory_error_in_the_read_that_follows_the_selection(self) -> None:
        """The same rule for a selected COMPLETION record (ORIGINAL_REQUEST §2: a later read
        failure changes the selection into no other outcome): a bound success selected, the
        next read fails -> COMPLETED on its own verdict, never `record_scan_incomplete`."""
        for seam in ("authoritative_text", "capture_raw"):
            with self.subTest(seam=seam):
                _s, result, state, prefix = self._await_with_seam(
                    f"f017-selected-completion-{seam}", self.SUCCESS_ROOT, seam=seam,
                    reached_when=self._is_bound_completion)
                self.assertTrue(state["reached"], "the production selector never selected the bound record")
                # an ELIGIBLE selection: a bound record, no refusal, no named non-success outcome
                self.assertIsNone(state["reached"][-1]["outcome"], state["reached"][-1])
                self.assertEqual(state["reached"][-1]["record"].get("is_error"), False, state["reached"][-1])
                self.assertIn(b'"is_error": false', prefix)
                self.assertEqual(result["state"], "COMPLETED", result)
                self.assertEqual((result.get("verdict") or {}).get("outcome"), "succeeded", result)
                self.assertNotEqual(result["evidence"]["provenance_outcome"], SCAN_INCOMPLETE, result["evidence"])

    def test_completion_reads_the_fenced_range_once_and_never_after_the_selection(self) -> None:
        """The option this run took (read REMOVED, not diagnostic-only): after the selector has
        returned, `completion()` issues no further read of the capture at all, so no later
        reader failure has a path to the selection.  This pins the implementation choice; if a
        diagnostic-only post-selection read is ever reintroduced, version this lock (never
        delete it) and keep the two dominance locks above, which do not depend on it."""
        _s, _result, state, _prefix = self._await_with_seam(
            "f017-no-read-after-selection", self.ROOT, seam="capture_raw", reached_when=self._is_refusal)
        self.assertEqual(state["faults"], [], "a capture read followed the positive selection")
        self.assertEqual(len(state["reached"]), 1, state["reached"])


# =====================================================================================
# F-017 (run_7859f202457c iteration 2; REVIEW_IMPLEMENTATION.md remaining branch) -- the
# PRE-BOUND caller path: await_completion() must not re-scan and erase a positive selection
# already reached over the SAME verified immutable boundary
# =====================================================================================
class F017PreBoundAwaitDominanceTests(_FramingDispatch):
    """The reviewer's `probe_prebound_await_contract`, ported as production-caller locks.

    Unlike `F017SelectedRefusalDominanceTests` (which enters `await_completion` with NO bound
    fence, so the fault falls on the first-and-only scan), here the PRODUCTION drain
    (`drain_after_exit`) is run BEFORE `await_completion`, exactly as a re-driven /
    already-drained session reaches it.  The fence is then bound and verified when
    `await_completion`'s initial `completion()` runs, so that first scan positively selects.
    `await_completion` then, on the proven exit, drains again (idempotent) and used to re-scan
    the SAME `[baseline, N)` unconditionally -- a `MemoryError` in that second settlement
    reader replaced the already-selected refusal (or bound completion) with
    `record_scan_incomplete` -> LOST.  Required (DESIGN 1.4 R1 / ORIGINAL_REQUEST 2): the
    positive selection over the verified boundary dominates; the redundant re-scan is skipped.

    The seam faults ONLY a `capture.raw` whose immediate caller is `completion` and ONLY after
    a positive selection has been reached -- fence verification, journal and drain reads are
    left intact, isolating the settlement-reader site exactly as the reviewer's probe does.
    The ordinary not-yet-bound path (no pre-await drain) is exercised too, and must be
    unchanged (the post-drain scan still supersedes a pre-fence non-selection)."""

    REFUSAL_ROOT = ("import os,json\n"
                    "sid=os.environ['OS48_TEST_SID']\n"
                    "for is_error in (False,True):\n"
                    " os.write(1,(json.dumps(dict(type='result',session_id=sid,is_error=is_error,result='body'))+'\\n').encode())\n")
    SUCCESS_ROOT = ("import os,json\n"
                    "sid=os.environ['OS48_TEST_SID']\n"
                    "os.write(1,(json.dumps(dict(type='result',session_id=sid,is_error=False,result='body'))+'\\n').encode())\n")

    def _spawn(self, run_id, root):
        agent = self.room.path / ("root-" + run_id + ".py")
        agent.write_text(root)
        session, _sentinel = spawn_session(self.room, "", run_id=run_id, argv=[PYTHON, str(agent)], image=PYTHON,
                                           binding_mode="session_field", binding_field="session_id")
        return session

    def _drain_before_await(self, session):
        """The production post-exit drain, run BEFORE await -- it binds and verifies the fence
        (the reviewer's construction; no fabricated selection, no assignment to `_boundary`)."""
        until = time.time() + 5
        while session._read_sentinel()["outcome"] != "exited" and time.time() < until:
            session.pump(timeout_ms=100)
        drained = session.drain_after_exit()
        self.assertIsNotNone(session._boundary, drained)
        self.assertEqual(drained.get("finality"), "capture_finalized", drained)

    @contextlib.contextmanager
    def _fault_in_completion_after_selection(self, session, *, reached_when, fault):
        """Wrap the REAL selector to record positive selections; fault a `capture.raw` whose
        immediate caller is `completion` (the settlement reader) once one has been reached."""
        state = {"reached": [], "faults": []}
        real_select = session.driver.select_completion
        real_raw = session.capture.raw

        def select(*args, **kwargs):
            selection = real_select(*args, **kwargs)
            if reached_when(selection):
                state["reached"].append(selection)
            return selection

        def failing_raw(cursor=0):
            if (fault and state["reached"]
                    and inspect.currentframe().f_back.f_code.co_name == "completion"):
                state["faults"].append("completion capture.raw after a positive selection")
                raise MemoryError("post-selection settlement-reader allocation seam")
            return real_raw(cursor)

        with patch.object(session.driver, "select_completion", select), \
                patch.object(session.capture, "raw", failing_raw):
            yield state

    def _verified_prefix(self, session):
        fence = session._boundary["fence"]
        n = fence["boundary"]["offset_n"]
        prefix = session.capture.raw()[:n]
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), fence["boundary"]["sha256_prefix"])
        return prefix

    @staticmethod
    def _is_refusal(selection):
        return selection.get("refusal") is not None

    @staticmethod
    def _is_bound_completion(selection):
        return selection.get("record") is not None and selection.get("refusal") is None

    def _run_prebound(self, run_id, root, *, fault, reached_when):
        session = self._spawn(run_id, root)
        self._drain_before_await(session)          # fence bound + verified BEFORE await
        with self._fault_in_completion_after_selection(session, reached_when=reached_when, fault=fault) as state:
            try:
                result = session.await_completion()
            except MemoryError as exc:
                self.fail("MemoryError escaped the production caller: " + str(exc))
        return session, result, state, self._verified_prefix(session)

    def test_a_prebound_selected_refusal_survives_a_later_completion_reader_memory_error(self):
        session, result, state, prefix = self._run_prebound(
            "f017-prebound-refusal-fault", self.REFUSAL_ROOT, fault=True, reached_when=self._is_refusal)
        self.assertTrue(state["reached"], "the production selector never reached the refusal")
        self.assertEqual(state["reached"][-1]["outcome"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, state["reached"][-1])
        # The seam is ARMED to fault any `completion()` capture.raw after the selection.  With
        # the fix it does NOT fire (the redundant re-scan is skipped); at 709cea0 / the i1 tree
        # it fires and this same disposition assertion goes RED (LOST).  Firing is the OLD
        # mechanism, so it is reported, not required (`state["faults"]`).
        self.assertIn(b'"is_error": true', prefix, "the refusal is not inside [0, N)")
        self.assertEqual(result["state"], "FAILED", result)
        self.assertEqual((result.get("verdict") or {}).get("reason"), capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, result)
        self.assertEqual(result.get("lost_reason"), "", result)
        self.assertEqual(result["evidence"]["provenance_outcome"], capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, result["evidence"])
        self.assertEqual((result["evidence"].get("refusal") or {}).get("source"), "error_field", result["evidence"])

    def test_the_prebound_refusal_control_settles_failed_without_the_fault(self):
        session, result, state, prefix = self._run_prebound(
            "f017-prebound-refusal-control", self.REFUSAL_ROOT, fault=False, reached_when=self._is_refusal)
        self.assertEqual(state["faults"], [])
        self.assertEqual(result["state"], "FAILED", result)
        self.assertEqual((result.get("verdict") or {}).get("reason"), capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, result)

    def test_a_prebound_selected_completion_survives_a_later_completion_reader_memory_error(self):
        session, result, state, prefix = self._run_prebound(
            "f017-prebound-completion-fault", self.SUCCESS_ROOT, fault=True, reached_when=self._is_bound_completion)
        self.assertTrue(state["reached"], "the production selector never selected the bound record")
        self.assertIsNone(state["reached"][-1]["outcome"], state["reached"][-1])
        # armed, reported not required (see the refusal fault lock)
        self.assertIn(b'"is_error": false', prefix)
        self.assertEqual(result["state"], "COMPLETED", result)
        self.assertEqual((result.get("verdict") or {}).get("outcome"), "succeeded", result)
        self.assertNotEqual(result["evidence"]["provenance_outcome"], SCAN_INCOMPLETE, result["evidence"])

    def test_the_prebound_completion_control_settles_completed_without_the_fault(self):
        session, result, state, prefix = self._run_prebound(
            "f017-prebound-completion-control", self.SUCCESS_ROOT, fault=False, reached_when=self._is_bound_completion)
        self.assertEqual(state["faults"], [])
        self.assertEqual(result["state"], "COMPLETED", result)
        self.assertEqual((result.get("verdict") or {}).get("outcome"), "succeeded", result)

    def test_the_prebound_second_scan_is_skipped_when_the_first_selected_over_the_same_fence(self):
        """The option this run took (skip the re-scan, not carry-forward): when await enters
        with the fence already bound and the first `completion()` positively selects over it,
        `await_completion` makes NO second `completion()` call -- so the armed post-selection
        settlement-reader seam never fires.  Counts production `completion()` calls and asserts
        the seam stayed silent while the refusal still settled FAILED.  Version -- never delete
        -- if the fix is ever changed to carry-forward-with-a-second-read; the four dominance
        locks above assert the disposition and do not depend on this count."""
        session = self._spawn("f017-prebound-skip", self.REFUSAL_ROOT)
        self._drain_before_await(session)
        calls = []
        real_completion = session.completion

        def traced_completion():
            calls.append(1)
            return real_completion()
        with patch.object(session, "completion", traced_completion), \
                self._fault_in_completion_after_selection(session, reached_when=self._is_refusal, fault=True) as state:
            result = session.await_completion()
        session.capture.raw  # (patched context has exited)
        self.assertEqual(len(calls), 1, f"await_completion re-scanned the same verified boundary ({len(calls)} completion() calls)")
        self.assertEqual(state["faults"], [], "a completion() capture.raw followed the positive selection")
        self.assertEqual(result["state"], "FAILED", result)
        self.assertEqual((result.get("verdict") or {}).get("reason"), capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, result)

    def test_the_ordinary_not_yet_bound_path_still_supersedes_the_pre_fence_scan(self):
        """No pre-await drain: `await_completion`'s first `completion()` runs with NO bound
        fence (no selection), and the post-drain scan is what settles the dispatch -- the
        legitimate supersession the double scan exists for.  A refusal root still settles
        FAILED / `refusal_in_boundary`, proving the skip is conditioned on a prior positive
        selection over the SAME verified boundary and does not defeat the normal path."""
        session = self._spawn("f017-not-yet-bound", self.REFUSAL_ROOT)
        real_completion = session.completion
        calls = []

        def traced_completion():
            ev = real_completion()
            calls.append(ev.get("boundary"))
            return ev
        with patch.object(session, "completion", traced_completion):
            result = session.await_completion()
        self.assertIsNone(calls[0], "the first completion() unexpectedly already had a bound fence")
        self.assertIsNotNone(session._boundary, "the drain never bound the fence")
        prefix = self._verified_prefix(session)
        self.assertIn(b'"is_error": true', prefix)
        self.assertEqual(result["state"], "FAILED", result)
        self.assertEqual((result.get("verdict") or {}).get("reason"), capture_mod.OUTCOME_REFUSAL_IN_BOUNDARY, result)


if __name__ == "__main__":
    unittest.main()
