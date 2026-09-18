"""OS-48 locks for REVIEW_IMPLEMENTATION.md (iteration 1) findings F-001..F-006, each re-running
the reviewer's construction through the REAL caller (`evidence/review_implementation_i1/probe_*`):

* F-001 the settled body / event come from the authoritative interval ``[baseline, N)`` and the
  sidecar FROZEN with the fence -- a later diagnostic-tail body changes nothing (live + adopted);
* F-002 the orphan caller binds its death witness to the HIGHEST owner's identity: a live g2 is
  `finalizer_alive` (no g3), a dead g2 is superseded with per-pid evidence, concurrent orphan
  claimants link exactly one generation, a published fence ends every claim;
* F-003 a release record is JOINED to the fence and the capture (tampering is named); a failed
  publication is `release_record_missing`, never `final`;
* F-004 positive partial PID / FD fills are `listallpids_partial` / `listing_partial_fill`;
* F-005 an unreadable start identity on ANY required axis (owner / emitter / reaper) refuses
  publication with `identity_unreadable`, and a fence carrying one is refused on load;
* F-006 the positive membership ledger names a detached helper and the teardown residual
  `descendants_unreaped` -- without ever signalling it.

RED at b9aecce: OS-48 names absent (import errors).  RED at the i1 tree: each construction
reproduced there (`probe_*.txt`).
"""
from __future__ import annotations

import errno
import json
import os
import pty as _pty
import signal
import struct
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.deterministic_workflow import standalone_capture as capture_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_identity as identity  # noqa: E402
from scripts.deterministic_workflow import standalone_pty as pty_supervisor  # noqa: E402
from scripts.deterministic_workflow import standalone_runtime as rt  # noqa: E402
from scripts.deterministic_workflow.standalone_profile import ResultBodySelector  # noqa: E402
from scripts.os48_cut_harness import (SUCCESS, pid_alive, settle, successor, wait_dead,  # noqa: E402
                                      wait_for)
from scripts.os48_lock_support import PYTHON, Room, sid_record, spawn_session  # noqa: E402

DARWIN_ONLY = unittest.skipUnless(sys.platform == "darwin", "OS-48 libproc evidence seams are darwin's (probe_03)")


def _late_body_agent(room: Room, trigger: Path, ack: Path, *, early: str, late: str,
                     late_type: str = "result", late_error: str = "false") -> Path:
    agent = room.path / "latebody.py"
    agent.write_text(
        "import os,time,json\n"
        "os.write(1,(json.dumps(dict(type='result',is_error=False,session_id=os.environ['OS48_TEST_SID'],result=%r))+'\\n').encode())\n"
        "if os.fork()==0:\n"
        "    while not os.path.exists(%r): time.sleep(.005)\n"
        "    n=os.write(1,(json.dumps(dict(type=%r,is_error=%s,session_id=os.environ['OS48_TEST_SID'],result=%r))+'\\n').encode())\n"
        "    open(%r,'w').write(str(n))\n"
        "    time.sleep(3);os._exit(0)\n"
        "os._exit(0)\n" % (early, str(trigger), late_type, late_error == "true", late, str(ack)))
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
# F-001 -- the settled body comes from [baseline, N) and the frozen sidecar, never the tail
# =====================================================================================
class F001AuthoritativeBodyTests(_RoomCase):
    def _run(self, *, late_type="result", late_error="false"):
        trigger, ack = self.room.path / "go", self.room.path / "ack"
        agent = _late_body_agent(self.room, trigger, ack, early="AUTHORITATIVE EARLY BODY",
                                 late="LATE DIAGNOSTIC BODY", late_type=late_type, late_error=late_error)
        session, sentinel = spawn_session(self.room, "", run_id="f001", argv=[PYTHON, str(agent)],
                                          image=PYTHON, binding_mode="session_field", binding_field="session_id")
        session.profile = replace(session.profile, result_body_records=(
            ResultBodySelector(channel="structured", record_type="result", body_field="result"),))
        session.driver = rt.drivers.driver_for(session.profile)
        completion = session.await_completion()
        self.assertEqual(completion["state"], "COMPLETED", completion)
        trigger.write_text("go")
        wait_for(ack.exists, seconds=5, what="the helper's late write")
        for _ in range(100):
            if b"LATE DIAGNOSTIC BODY" in session.capture.raw():
                break
            session.pump(timeout_ms=50)
        raw = session.capture.raw()
        self.assertGreaterEqual(raw.index(b"LATE DIAGNOSTIC BODY"), int(session._boundary["offset_n"]))
        return session, sentinel, completion

    def test_the_real_settlement_parses_the_early_body_not_the_late_one(self) -> None:
        """The reviewer's `probe_late_result_body` through the REAL `_settle`: the parser input,
        the settled event and the journaled provenance all come from `[baseline, N)`."""
        session, _sentinel, completion = self._run()
        seen: list[str] = []

        def parser(attempt, intent):
            seen.append(attempt.body)
            return {"status": "COMPLETE", "body_seen": attempt.body}
        session.intent["command_id"] = "c"
        session.intent["payload_digest"] = "0" * 64
        session.state = "RUNNING"
        with patch.object(session, "_reclaim", return_value={"reaped": True}):
            event = session._settle(completion["evidence"], lease_token=None, result_parser=parser,
                                    verdict=completion["verdict"])
        self.assertEqual(seen, ["AUTHORITATIVE EARLY BODY"])
        self.assertEqual(event["result"]["body_seen"], "AUTHORITATIVE EARLY BODY")
        rows = [r for r in session.journal.rows_for(session.intent_id) if r["kind"] == "SETTLEMENT_OBSERVED"]
        prov = rows[-1]["source_vocabulary"]["result_body_provenance"]
        n = int(session._boundary["offset_n"])
        self.assertEqual(prov["interval"]["offset_n"], n)
        self.assertEqual(prov["interval"]["sha256"], capture_mod.prefix_digest(session.capture.raw(), n)
                         if prov["interval"]["baseline"] == 0 else prov["interval"]["sha256"])

    def test_the_adopted_settlement_parses_the_same_early_body(self) -> None:
        session, sentinel, _completion = self._run()
        s = successor(self.room, self._info(session, sentinel))
        s.profile = replace(s.profile, result_body_records=(
            ResultBodySelector(channel="structured", record_type="result", body_field="result"),))
        s.driver = rt.drivers.driver_for(s.profile)
        out = settle(s)
        self.assertEqual(out["state"], "COMPLETED", out)
        self.assertEqual(out["event"]["result"]["body"], "AUTHORITATIVE EARLY BODY")

    def test_a_late_refusal_after_the_boundary_cannot_fail_the_settlement(self) -> None:
        session, _sentinel, completion = self._run(late_error="true")
        self.assertEqual(completion["state"], "COMPLETED")
        out = settle(session, completion)
        self.assertEqual(out["state"], "COMPLETED", out)

    def test_the_sidecar_is_frozen_with_the_fence(self) -> None:
        """# superseded by OS-48 i3 (REVIEW_IMPLEMENTATION_iteration2 F-001): the sidecar is
        # snapshotted by the WATCHER at the boundary (before the marker), not at publication.
        A `-o` sidecar body that existed AT N is digested into the fence from that snapshot;
        the live settlement reads the snapshot bytes, a file mutated after N changes nothing,
        and an adopted session settles the same boundary body from the same snapshot."""
        sidecar = self.room.path / "last_message.md"
        sidecar.write_text("FROZEN SIDECAR BODY")
        session, sentinel = spawn_session(self.room, SUCCESS + "exit 0\n", run_id="f001sc",
                                          binding_mode="session_field", binding_field="session_id",
                                          pump_until_sentinel=False, sidecar_path=str(sidecar))
        session.profile = replace(session.profile, output_last_message_path=str(sidecar),
                                  result_body_records=(ResultBodySelector(channel="structured",
                                                                          record_type="result", body_field="result"),))
        session.driver = rt.drivers.driver_for(session.profile)
        completion = session.await_completion()
        self.assertEqual(completion["state"], "COMPLETED", completion)
        fence = capture_mod.read_capture_fence(session._fence_path(), fence=session.fence)["record"]
        self.assertEqual(fence["sidecar"]["state"], capture_mod.SIDECAR_STATE_PRESENT, fence["sidecar"])
        self.assertEqual(fence["sidecar"]["bytes"], len("FROZEN SIDECAR BODY"))
        sidecar.write_text("MUTATED AFTER THE FENCE")
        out = settle(session, completion)
        # superseded by OS-48 i4 (REVIEW_IMPLEMENTATION_iteration3 F-001 (b), option ii): the
        # snapshot is R3's presence fact; the settled body is the in-boundary stream record
        self.assertNotIn("SIDECAR BODY", out["event"]["result"]["body"], out)
        s = successor(self.room, self._info(session, sentinel))
        s.last_message_path = str(sidecar)
        s.profile = session.profile
        s.driver = rt.drivers.driver_for(s.profile)
        out2 = settle(s)
        self.assertEqual(out2["state"], "COMPLETED", out2)
        self.assertNotIn("SIDECAR BODY", out2["event"]["result"]["body"], out2)
        self.assertEqual(s._sidecar_state, capture_mod.SIDECAR_STATE_PRESENT)


# =====================================================================================
# F-002 -- the orphan caller's witness is bound to the highest owner
# =====================================================================================
class F002WitnessBindingTests(_RoomCase):
    FENCE = "s:i"

    def _ident(self, pid: int, start: int) -> dict:
        return dict(pid=pid, start_id=start, boot_id=pty_supervisor.host_boot_id(),
                    incarnation=self.FENCE, source="test", schema="os48.process_identity.v1")

    def _gen(self, n: int, owner: dict, pred: dict | None = None) -> dict:
        return capture_mod.make_owner_generation(fence=self.FENCE, generation=n,
                                                 owner_role=capture_mod.OWNER_SUCCESSOR, owner=owner,
                                                 claim_reason="test", superseded=pred,
                                                 death_evidence="pinned", claimed_at="t")

    def _ev(self, n: int, pred: dict | None = None) -> dict:
        return dict(predecessor_generation=n, predecessor=pred or {}, death_witness="final",
                    relinquish_record=False, highest_owner_alive=False)

    def _dead_witnessed(self):
        """A REAL dead g1 owner with a REAL kernel death witness (kqueue / pidfd)."""
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(w)
            os.read(r, 1)
            os._exit(0)
        os.close(r)
        dead = self._ident(pid, pty_supervisor.proc_start_ticks(pid))
        witness = pty_supervisor._ParentWitness(dead)
        os.close(w)
        os.waitpid(pid, 0)
        self.assertEqual(witness.fired(1.0), "final")
        return dead, witness

    def _orphan(self, capture: bytes, witness, **over):
        master, slave = _pty.openpty()
        self.addCleanup(lambda: os.close(master))
        kwargs = dict(capture=capture, fence=self.FENCE, fence_nonce="a" * 32, code=0, marker_written=True,
                      sentinel=None, witness=witness, budget_s=0.01, host_boot_id=pty_supervisor.host_boot_id(),
                      agent_pid=os.getpid(), agent_start_id=pty_supervisor.proc_start_ticks(os.getpid()))
        kwargs.update(over)
        with patch.object(pty_supervisor, "_write_marker_bounded", return_value=True):
            out = pty_supervisor._orphan_finalize(master, slave, None, **kwargs)
        try:
            os.close(slave)
        except OSError:
            pass
        return out

    def _capture(self) -> bytes:
        cap = os.fsencode(str(self.room.path / "capture.log"))
        Path(os.fsdecode(cap)).write_bytes(b'{"type":"result","is_error":false}\n'
                                           + capture_mod.marker_bytes("a" * 32) + capture_mod.release_marker_bytes("a" * 32))
        return cap

    def test_a_dead_g1_witness_never_supersedes_a_live_g2(self) -> None:
        """The reviewer's `probe_real_contracts.orphan_wrong_witness`: g1 dead + witnessed, g2 = this
        LIVE process -> the real orphan caller refuses `finalizer_alive`; no g3, no fence."""
        cap = self._capture()
        d = str(self.room.path)
        dead, witness = self._dead_witnessed()
        live = self._ident(os.getpid(), pty_supervisor.proc_start_ticks(os.getpid()))
        self.assertIsNone(capture_mod.claim_generation(d, "i", self._gen(1, dead), self._ev(0)))
        self.assertIsNone(capture_mod.claim_generation(d, "i", self._gen(2, live, dead), self._ev(1, dead)))
        out = self._orphan(cap, witness)
        self.assertEqual(out["outcome"], capture_mod.OUTCOME_FINALIZER_ALIVE, out)
        self.assertEqual(out["highest_owner_alive"], True)
        self.assertEqual(capture_mod.read_generations(d, "i")[0], 2)
        self.assertEqual(capture_mod.read_capture_fence(capture_mod.capture_fence_path(cap, "i"), fence=self.FENCE)["outcome"], "absent")

    def test_a_dead_g2_is_superseded_only_with_its_own_per_pid_evidence(self) -> None:
        """g2's owner is a different, positively dead incarnation: the orphan obtains evidence
        for THAT pid (absent), pins it and links g3 naming g2's identity and evidence."""
        cap = self._capture()
        d = str(self.room.path)
        dead, witness = self._dead_witnessed()
        dead2, _w2 = self._dead_witnessed()
        self.assertIsNone(capture_mod.claim_generation(d, "i", self._gen(1, dead), self._ev(0)))
        self.assertIsNone(capture_mod.claim_generation(d, "i", self._gen(2, dead2, dead), self._ev(1, dead)))
        out = self._orphan(cap, witness)
        self.assertEqual(out["role"], "finalizer", out)
        highest, g3, _ = capture_mod.read_generations(d, "i")
        self.assertEqual(highest, 3)
        self.assertEqual(int(g3["superseded"]["pid"]), int(dead2["pid"]))
        self.assertEqual(int(g3["superseded"]["start_id"]), int(dead2["start_id"]))
        self.assertEqual(g3["death_evidence"], "esrch_or_zombie_pinned_pid")

    def test_an_unreadable_g2_owner_refuses_by_name(self) -> None:
        cap = self._capture()
        d = str(self.room.path)
        dead, witness = self._dead_witnessed()
        self.assertIsNone(capture_mod.claim_generation(d, "i", self._gen(1, dead), self._ev(0)))
        self.assertIsNone(capture_mod.claim_generation(d, "i", self._gen(2, self._ident(os.getppid(), 12345), dead), self._ev(1, dead)))
        # the evidence source cannot read g2's owner (EPERM-shaped): the seam is the platform
        # read itself, so the construction holds on a host where every pid is readable (root)
        with patch.object(pty_supervisor, "read_identity",
                          lambda pid: {"start_id": 0, "start_state": "unreadable", "boot_id": ""}):
            out = self._orphan(cap, witness)
        self.assertEqual(out["outcome"], "identity_unreadable", out)
        self.assertEqual(capture_mod.read_generations(d, "i")[0], 2)

    def test_a_published_fence_ends_every_claim(self) -> None:
        cap = self._capture()
        d = str(self.room.path)
        dead, witness = self._dead_witnessed()
        self.assertIsNone(capture_mod.claim_generation(d, "i", self._gen(1, dead), self._ev(0)))
        record = capture_mod.make_capture_fence(
            fence=self.FENCE, emitter=self._ident(os.getpid(), 5), emitter_pgid=1, offset_n=35, marker_len=51,
            marker_nonce="a" * 32, sha256_prefix=capture_mod.prefix_digest(Path(os.fsdecode(cap)).read_bytes(), 35),
            tail_bytes_at_publish=0, exit_how="waitpid_by_parent", exit_code=0, reaped_by=None,
            owner={"owner_role": capture_mod.OWNER_SUPERVISOR, "generation": 1, "owner": dead},
            evidence_source="test", provenance=["test"], published_at="t")
        self.assertTrue(capture_mod.write_capture_fence(capture_mod.capture_fence_path(cap, "i"), record))
        out = self._orphan(cap, witness)
        self.assertEqual(out["role"], "custodian", out)
        self.assertEqual(capture_mod.read_generations(d, "i")[0], 1)

    def test_concurrent_orphan_claimants_link_exactly_one_generation(self) -> None:
        cap = self._capture()
        d = str(self.room.path)
        dead, _w = self._dead_witnessed()
        self.assertIsNone(capture_mod.claim_generation(d, "i", self._gen(1, dead), self._ev(0)))
        go_r, go_w = os.pipe()
        pipes = []
        for i in range(6):
            r, w = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(r)
                os.close(go_w)
                os.read(go_r, 1)
                witness = pty_supervisor._ParentWitness(dead)      # ESRCH at registration: witnessed
                out = self._orphan(cap, witness)
                os.write(w, json.dumps({"role": out.get("role"), "outcome": out.get("outcome")}).encode())
                os._exit(0)
            os.close(w)
            pipes.append((pid, r))
        os.close(go_r)
        os.write(go_w, b"g" * 6)
        os.close(go_w)
        reports = []
        for pid, r in pipes:
            reports.append(json.loads(os.read(r, 4096) or b"{}"))
            os.close(r)
            os.waitpid(pid, 0)
        winners = [x for x in reports if x.get("role") == "finalizer"]
        self.assertEqual(len(winners), 1, reports)
        # losers: the link race (`owner_conflict`), or the winner seen ALIVE as g2's owner
        # (`finalizer_alive`), or -- after its fence -- a custodian; never a second claim
        self.assertTrue(all(x.get("outcome") in (None, capture_mod.OUTCOME_OWNER_CONFLICT,
                                                 capture_mod.OUTCOME_FINALIZER_ALIVE) for x in reports), reports)
        self.assertEqual(capture_mod.read_generations(d, "i")[0], 2)


# =====================================================================================
# F-003 -- the release record is joined to the fence; a failed publication is named
# =====================================================================================
class F003ReleaseJoinTests(_RoomCase):
    def test_a_release_record_is_verified_against_fence_and_capture(self) -> None:
        session, _sentinel = spawn_session(self.room, SUCCESS + "exit 0\n", run_id="f003",
                                           binding_mode="session_field", binding_field="session_id")
        self.assertEqual(session.drain_after_exit(budget_ms=3000)["ended"], "marker")
        released = session._release_two_phase()
        self.assertEqual(released["state"], capture_mod.EVIDENCE_FINAL, released)
        rec = capture_mod.read_release_record(session._release_path(), fence=session.fence)["record"]
        raw = session.capture.raw()
        fence = session._boundary["fence"]
        ok = capture_mod.verify_release_record(rec, capture=raw, fence_path=session._fence_path(), fence_record=fence)
        self.assertTrue(ok["matches"], ok)
        for field, value, reason in (("offset_r", int(rec["offset_r"]) + 1, "release_marker_absent_or_moved"),
                                     ("retained_tail_sha256", "0" * 64, "retained_tail_digest_mismatch"),
                                     ("fence_file_sha256", "0" * 64, "fence_file_digest_mismatch"),
                                     ("release_nonce", "b" * 32, "release_nonce_mismatch")):
            with self.subTest(field=field):
                tampered = dict(rec, **{field: value})
                bad = capture_mod.verify_release_record(tampered, capture=raw, fence_path=session._fence_path(), fence_record=fence)
                self.assertEqual((bad["matches"], bad["reason"]), (False, reason))

    def test_a_failed_publication_is_never_final(self) -> None:
        """The reviewer's ENOSPC construction: the record cannot be written -> state unknown,
        `diagnostic_tail_unaccounted` + `release_record_missing`; the fence and [0,N) untouched."""
        session, _sentinel = spawn_session(self.room, SUCCESS + "exit 0\n", run_id="f003b",
                                           binding_mode="session_field", binding_field="session_id")
        self.assertEqual(session.await_completion()["state"], "COMPLETED")
        before = Path(os.fsdecode(session._fence_path())).read_bytes()
        with patch.object(capture_mod, "write_release_record", side_effect=OSError(errno.ENOSPC, "disk full")):
            released = session._release_two_phase()
        self.assertNotEqual(released["state"], capture_mod.EVIDENCE_FINAL, released)
        self.assertEqual(released["outcome"], capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED)
        self.assertEqual(released["release_record"], capture_mod.OUTCOME_RELEASE_RECORD_MISSING)
        self.assertEqual(capture_mod.read_release_record(session._release_path(), fence=session.fence)["outcome"], "absent")
        self.assertEqual(Path(os.fsdecode(session._fence_path())).read_bytes(), before)


# =====================================================================================
# F-004 -- positive partial fills are named, never clean
# =====================================================================================
@DARWIN_ONLY
class F004PartialFillTests(unittest.TestCase):
    def test_a_short_positive_pid_fill_is_listallpids_partial(self) -> None:
        """The reviewer's `probe_short_pid_fill`: count 100, positive fill 1 -> named partial;
        the diagnostic is `unreadable` with the reason, never `none_observed`."""
        real = pty_supervisor._LIBPROC

        class Short:
            def __getattr__(self, name):
                return getattr(real, name)

            def proc_listallpids(self, buf, size):
                if buf is None:
                    return 100
                buf[0] = os.getpid()
                return 1
        master, slave = _pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        with patch.object(pty_supervisor, "_LIBPROC", Short()):
            pids, reason = pty_supervisor._libproc_list_all_pids()
            holders = pty_supervisor.slave_device_holders(os.ttyname(slave))
        self.assertIsNone(pids)
        self.assertEqual(reason, "listallpids_partial")
        self.assertEqual(holders["state"], "unreadable", holders)
        self.assertEqual(holders["unenumerable"][0]["errno"], "listallpids_partial")

    def test_a_whole_entry_fd_prefix_omitting_the_live_slave_is_listing_partial(self) -> None:
        """The reviewer's `probe_short_fd_fill`: a real holder; the listing truncated to the
        entries below the slave fd (omitting it) -> the independent per-index walk finds the
        omitted vnode fd -> `listing_partial_fill`, never `clean`."""
        master, slave = _pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        r, w = os.pipe()
        holder = os.fork()
        if holder == 0:
            os.close(w)
            os.read(r, 1)
            os._exit(0)
        os.close(r)
        self.addCleanup(lambda: (os.write(w, b"x"), os.close(w), os.waitpid(holder, 0)))
        ref, _reason = pty_supervisor._slave_reference_devino(os.ttyname(slave))
        real = pty_supervisor._LIBPROC
        observed = {}

        class Short:
            def __getattr__(self, name):
                return getattr(real, name)

            def proc_pidinfo(self, pid, flavor, arg, buf, size):
                got = real.proc_pidinfo(pid, flavor, arg, buf, size)
                if pid == holder and flavor == pty_supervisor._PROC_PIDLISTFDS and buf is not None and got > 8:
                    raw = bytes(buf)[:got]
                    fds = [struct.unpack_from("<i", raw, i * 8)[0] for i in range(got // 8)]
                    count = max(1, sum(fd < slave for fd in fds))
                    observed.update(native=got, short=count * 8, slave_in_prefix=slave in fds[:count])
                    return count * 8
                return got
        with patch.object(pty_supervisor, "_LIBPROC", Short()):
            scan = pty_supervisor._scan_process_for_slave(holder, ref, set())
        self.assertFalse(observed.get("slave_in_prefix", True), observed)
        self.assertEqual(scan, ("unstable", "listing_partial_fill"), (scan, observed))
        # and without the seam the same holder is a MATCH (the walk agrees with the listing)
        self.assertEqual(pty_supervisor._scan_process_for_slave(holder, ref, set()), ("match", holder))


# =====================================================================================
# F-005 -- unreadable required identity refuses publication and load
# =====================================================================================
class F005IdentityRequiredTests(_RoomCase):
    def test_an_unreadable_start_identity_is_lost_not_completed(self) -> None:
        """The reviewer's `probe_unreadable_capture_identity`: the platform start read answers 0
        -> no claim, no fence, LOST `identity_unreadable`."""
        with patch.object(pty_supervisor, "proc_start_ticks", return_value=0):
            session, _sentinel = spawn_session(self.room, SUCCESS + "exit 0\n", run_id="f005",
                                               binding_mode="session_field", binding_field="session_id")
            result = session.await_completion()
        self.assertEqual((result["state"], result.get("lost_reason")), ("LOST", identity.IDENTITY_UNREADABLE), result)
        self.assertEqual(capture_mod.read_capture_fence(session._fence_path(), fence=session.fence)["outcome"], "absent")
        self.assertEqual(capture_mod.read_generations(session._owner_dir(), session.incarnation)[0], 0)

    def test_each_required_axis_refuses_publication_by_name(self) -> None:
        for axis in ("emitter", "reaped_by", "owner"):
            with self.subTest(axis=axis):
                session, _sentinel = spawn_session(self.room, SUCCESS + "exit 0\n", run_id=f"f005{axis}",
                                                   binding_mode="session_field", binding_field="session_id")
                if axis == "emitter":
                    session.record["proc_start_ticks"] = 0
                elif axis == "reaped_by":
                    session.pty["watcher_start_id"] = 0
                else:
                    session._self_identity = lambda role, _s=session: identity.process_identity(
                        pid=os.getpid(), start_id=0, boot_id="", incarnation=_s.fence, source="test")
                drained = session.drain_after_exit(budget_ms=3000)
                self.assertEqual(drained.get("outcome"), identity.IDENTITY_UNREADABLE, drained)
                self.assertIn(axis, drained.get("finality_detail", ""))
                self.assertFalse(os.path.exists(session._fence_path()))

    def test_a_fence_carrying_an_unreadable_identity_is_refused_on_load(self) -> None:
        session, sentinel = spawn_session(self.room, SUCCESS + "exit 0\n", run_id="f005load",
                                          binding_mode="session_field", binding_field="session_id")
        self.assertTrue(rt._stream_is_final(session.drain_after_exit(budget_ms=3000)))
        path = Path(os.fsdecode(session._fence_path()))
        record = json.loads(path.read_text())
        for axis in ("emitter", "owner", "reaped_by"):
            with self.subTest(axis=axis):
                bad = json.loads(json.dumps(record))
                if axis == "emitter":
                    bad["emitter"]["start_id"] = 0
                elif axis == "owner":
                    bad["owner"]["owner"]["boot_id"] = ""
                else:
                    bad["exit"]["reaped_by"]["start_id"] = 0
                bound = capture_mod.fence_matches(bad, capture=session.capture.path, sentinel_code=0, sentinel_present=True)
                self.assertEqual((bound["matches"], bound["reason"], bound.get("axis")), (False, "identity_unreadable", axis))
        # the adopted path names it too
        path.write_text(json.dumps(dict(record, emitter=dict(record["emitter"], start_id=0))))
        s = successor(self.room, self._info(session, sentinel))
        drained = s.drain_after_exit(budget_ms=1000)
        self.assertEqual(drained.get("outcome"), identity.IDENTITY_UNREADABLE, drained)
        self.assertFalse(rt._stream_is_final(drained))


# =====================================================================================
# F-006 -- the positive membership ledger and the named residual
# =====================================================================================
class F006MembershipLedgerTests(_RoomCase):
    def _detached_agent(self, info: Path, *, helper_sleep: float = 30.0) -> Path:
        agent = self.room.path / "members.py"
        agent.write_text(
            "import os,time,json,signal\nr,w=os.pipe()\n"
            "if os.fork()==0:\n    os.close(r);os.setsid();signal.signal(signal.SIGHUP,signal.SIG_IGN)\n"
            "    open(%r,'w').write(json.dumps(dict(pid=os.getpid())))\n"
            "    os.write(w,b'r');os.close(w);time.sleep(%r);os._exit(0)\n"
            "os.close(w);os.read(r,1);os.close(r)\n"
            # the root stays alive briefly after the fork: discovery is TRIGGERED by NOTE_FORK
            # and attributes a child only to a live/zombie parent incarnation (DESIGN §2.2:
            # a parent gone before the walk is a permitted miss, not this lock's subject)
            "time.sleep(0.3)\n"
            "os.write(1,(json.dumps(dict(type='result',is_error=False,session_id=os.environ['OS48_TEST_SID']))+'\\n').encode());os._exit(0)\n"
            % (str(info), helper_sleep))
        return agent

    def test_a_detached_helper_is_a_member_and_a_named_residual_never_signalled(self) -> None:
        """The reviewer's `probe_membership_real`: a setsid helper that outlives the dispatch is
        in `members.<inc>.jsonl` with its start identity, `_reclaim` journals
        `descendants_unreaped` naming it, and no signal reaches it (kill spy)."""
        info = self.room.path / "helper.json"
        agent = self._detached_agent(info)
        sent: list = []
        real_kill = os.kill

        def spy(pid, sig):
            if sig != 0:
                sent.append((pid, sig))
            return real_kill(pid, sig)
        session, _sentinel = spawn_session(self.room, "", run_id="f006", argv=[PYTHON, str(agent)],
                                           image=PYTHON, binding_mode="session_field", binding_field="session_id")
        helper = json.loads(info.read_text())["pid"]
        start = pty_supervisor.proc_start_ticks(helper)
        self.addCleanup(lambda: real_kill(helper, signal.SIGKILL) if pty_supervisor.proc_start_ticks(helper) == start else None)
        self.assertEqual(session.await_completion()["state"], "COMPLETED")
        with patch.object(os, "kill", spy):
            session._reclaim(reason="lock")
        members = pty_supervisor.read_members(session._members_path())
        observed = {int(m["identity"]["pid"]): m for m in members if m["event"] == "observed"}
        self.assertIn(helper, observed, members)
        self.assertEqual(observed[helper]["role"], "descendant")
        self.assertEqual(int(observed[helper]["identity"]["start_id"]), start)
        self.assertEqual(observed[int(session.record["pid"])]["role"], "agent")
        rows = [r for r in session.journal.rows_for(session.intent_id) if r.get("event") == "descendants_unreaped"]
        self.assertEqual(len(rows), 1, "no named residual")
        alive = rows[0]["source_vocabulary"]["alive"]
        self.assertEqual([a["pid"] for a in alive], [helper])
        self.assertEqual(int(alive[0]["start_id"]), start)
        self.assertEqual([s for s in sent if s[0] == helper], [], "the helper was signalled")
        self.assertTrue(pid_alive(helper))

    def test_a_helper_that_exits_leaves_no_residual(self) -> None:
        info = self.room.path / "helper2.json"
        agent = self._detached_agent(info, helper_sleep=0.3)
        session, _sentinel = spawn_session(self.room, "", run_id="f006b", argv=[PYTHON, str(agent)],
                                           image=PYTHON, binding_mode="session_field", binding_field="session_id")
        helper = json.loads(info.read_text())["pid"]
        self.assertEqual(session.await_completion()["state"], "COMPLETED")
        wait_for(lambda: pty_supervisor._pid_presence(helper) == "absent" or pty_supervisor.proc_start_ticks(helper) == 0,
                 seconds=10, what="the helper's exit")
        session._reclaim(reason="lock")
        residual = session.membership_residual()
        self.assertEqual(residual["alive"], [], residual)
        self.assertIn(helper, residual["exited"], residual)
        rows = [r for r in session.journal.rows_for(session.intent_id) if r.get("event") == "descendants_unreaped"]
        # superseded by OS-48 i7 (conservative model): the exited helper leaves no ALIVE
        # residual, but the root's fork stays a named unknown (`fork_coalesced`), so the row
        # exists with outcome `descendants_unknown` and an empty alive set.
        # (Linux has no fork events and the helper exited under observation: no row there.)
        if sys.platform == "darwin":
            self.assertEqual(len(rows), 1, rows)
            self.assertEqual(rows[0]["source_vocabulary"]["outcome"], capture_mod.OUTCOME_DESCENDANTS_UNKNOWN)
            self.assertEqual(rows[0]["source_vocabulary"]["alive"], [])
        else:
            self.assertEqual([r["source_vocabulary"]["alive"] for r in rows], [[]] * len(rows))

    def test_an_unreadable_member_stays_unknown(self) -> None:
        info = self.room.path / "helper3.json"
        agent = self._detached_agent(info)
        session, _sentinel = spawn_session(self.room, "", run_id="f006c", argv=[PYTHON, str(agent)],
                                           image=PYTHON, binding_mode="session_field", binding_field="session_id")
        helper = json.loads(info.read_text())["pid"]
        self.addCleanup(lambda: os.kill(helper, signal.SIGKILL))
        self.assertEqual(session.await_completion()["state"], "COMPLETED")
        session._identity_reader = lambda pid: {"start_id": 0, "start_state": "unreadable", "boot_id": ""}
        residual = session.membership_residual()
        # superseded by OS-48 i7 (conservative model): the residual also carries the
        # discovery entry (the root forked: `fork_coalesced`); the member-level unknown is
        # still exactly the helper.
        self.assertEqual([u["pid"] for u in residual["unknown"] if "pid" in u], [helper], residual)
        self.assertEqual(residual["alive"], [])

    def test_membership_never_widens_the_signal_authority(self) -> None:
        import inspect
        src = inspect.getsource(pty_supervisor.signal_target) + inspect.getsource(pty_supervisor._ControlServer)
        self.assertNotIn("members", src)
        self.assertNotIn("read_members", inspect.getsource(rt.StandaloneSession._watcher_signal))


if __name__ == "__main__":
    unittest.main()
