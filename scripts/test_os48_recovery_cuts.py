"""OS-48 recovery / succession cuts (run_f820764749d6, DESIGN §1.7 C1-C7, §2.5, §7 L-09 /
L-09b): the finalizer-owner generation chain `owner.<inc>.g<n>` is claimed EXCLUSIVELY
(tmp + fsync + link), a claim is bound to the pinned predecessor obtained WITH the death /
relinquish evidence and may link only predecessor+1, a published fence ends every claim
(fence-first: `fence_published_no_claim`), and guard EOF alone never authorises succession
(`succession_unwitnessed`).  Crash cuts are `_crash_at` seams on the writer path (as
test_os37_round9_iter2 injects), never timing.

RED at b9aecce: none of these records exist; the successor settled from a legacy
`capture_finalized` record or refused `stream_end_unproven` (round-9 iter2 F1 cut).
"""
from __future__ import annotations

import json
import multiprocessing
import os
import sys
import time
import unittest
from pathlib import Path

from scripts.deterministic_workflow import standalone_capture as capture_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_journal as journal_mod  # noqa: E402
from scripts.deterministic_workflow import standalone_pty as pty_supervisor  # noqa: E402
from scripts.deterministic_workflow import standalone_runtime as rt  # noqa: E402
from scripts.os48_lock_support import Room, sh_profile, sid_record, spawn_session  # noqa: E402

FENCE = "sess:inc1"
INC = "inc1"


def _owner(pid: int, start_id: int = 1) -> dict:
    return {"pid": pid, "start_id": start_id, "boot_id": "b", "incarnation": FENCE,
            "schema": "os48.process_identity.v1", "source": "test"}


def _generation(n: int, owner: dict, *, superseded: dict | None = None,
                death_evidence: str = "") -> dict:
    return capture_mod.make_owner_generation(
        fence=FENCE, generation=n, owner_role=capture_mod.OWNER_SUCCESSOR, owner=owner,
        claim_reason="test", superseded=superseded, death_evidence=death_evidence, claimed_at="t")


def _evidence(predecessor_generation: int, predecessor: dict | None, *, relinquish=False,
              witness="final", alive=None) -> dict:
    return {"predecessor_generation": predecessor_generation, "predecessor": predecessor or {},
            "relinquish_record": relinquish, "death_witness": witness,
            "highest_owner_alive": alive}


class _Dir(unittest.TestCase):
    def setUp(self) -> None:
        self.room = Room()
        self.addCleanup(self.room.close)
        self.dir = self.room.path


# =====================================================================================
# L-09 -- the generation chain: exclusive, pinned, predecessor+1 only
# =====================================================================================
class L09GenerationChainTests(_Dir):
    def test_the_first_claim_links_g1_exclusively(self) -> None:
        g1 = _generation(1, _owner(100))
        self.assertIsNone(capture_mod.claim_generation(self.dir, INC, g1, _evidence(0, None)))
        highest, rec, state = capture_mod.read_generations(self.dir, INC)
        self.assertEqual((highest, state), (1, capture_mod.EVIDENCE_FINAL))
        self.assertEqual(rec["owner"]["pid"], 100)
        # a second g1 by anyone loses by name and changes nothing
        self.assertEqual(capture_mod.claim_generation(self.dir, INC, _generation(1, _owner(200)),
                                                      _evidence(0, None)),
                         capture_mod.OUTCOME_OWNER_CONFLICT)
        self.assertEqual(capture_mod.read_generations(self.dir, INC)[1]["owner"]["pid"], 100)

    def test_a_torn_initial_claim_leaves_no_generation(self) -> None:
        """C5-shape at the writer: the crash lands between the tmp and the link.  The tmp is
        never authoritative: `read_generations` sees NO generation and a successor claims g1."""
        tmp = capture_mod.owner_generation_path(self.dir, INC, 1) + b".tmp.999"
        Path(os.fsdecode(tmp)).write_bytes(json.dumps(_generation(1, _owner(100))).encode())
        highest, rec, state = capture_mod.read_generations(self.dir, INC)
        self.assertEqual((highest, rec, state), (0, None, capture_mod.EVIDENCE_FINAL))
        self.assertIsNone(capture_mod.claim_generation(self.dir, INC, _generation(1, _owner(200)),
                                                       _evidence(0, None)))

    def test_a_claim_is_bound_to_the_witnessed_predecessor_and_links_only_plus_one(self) -> None:
        """F-004: the evidence pins (predecessor generation, predecessor identity); the record
        must name that predecessor as `superseded` and be exactly predecessor+1; a claim
        attempted after another generation superseded the predecessor is `owner_conflict`."""
        dead = _owner(100)
        self.assertIsNone(capture_mod.claim_generation(self.dir, INC, _generation(1, dead),
                                                       _evidence(0, None)))
        ev = _evidence(1, dead, witness="final", alive=False)
        self.assertEqual(capture_mod.claim_target(ev), 2)
        # wrong target
        self.assertEqual(capture_mod.validate_claim(_generation(3, _owner(200), superseded=dead), ev,
                                                    highest_linked_now=1),
                         capture_mod.OUTCOME_OWNER_CONFLICT)
        # wrong predecessor named
        self.assertEqual(capture_mod.validate_claim(_generation(2, _owner(200), superseded=_owner(555)),
                                                    ev, highest_linked_now=1),
                         capture_mod.OUTCOME_OWNER_CONFLICT)
        # the right claim
        g2 = _generation(2, _owner(200), superseded=dead, death_evidence="esrch")
        self.assertIsNone(capture_mod.claim_generation(self.dir, INC, g2, ev))
        # a SECOND successor of the SAME predecessor, with the SAME (stale) evidence, loses:
        # the predecessor was superseded already (highest is 2, evidence pins 1).
        g2b = _generation(2, _owner(300), superseded=dead, death_evidence="esrch")
        self.assertEqual(capture_mod.claim_generation(self.dir, INC, g2b, ev),
                         capture_mod.OUTCOME_OWNER_CONFLICT)
        # and it may not "skip ahead" to g3 on the g1 witness either
        g3 = _generation(3, _owner(300), superseded=dead, death_evidence="esrch")
        self.assertEqual(capture_mod.claim_generation(self.dir, INC, g3, ev),
                         capture_mod.OUTCOME_OWNER_CONFLICT)
        self.assertEqual(capture_mod.read_generations(self.dir, INC)[0], 2)

    def test_sixteen_simultaneous_dead_owner_successors_link_exactly_one(self) -> None:
        """probe_d9 / d12: 16 processes race to succeed the same dead g1 with the same
        evidence; exactly one g2 links, every loser is `owner_conflict`, no torn record."""
        dead = _owner(100)
        self.assertIsNone(capture_mod.claim_generation(self.dir, INC, _generation(1, dead),
                                                       _evidence(0, None)))
        ev = _evidence(1, dead, witness="final", alive=False)
        ctx = multiprocessing.get_context("fork")
        queue = ctx.Queue()

        def racer(n: int) -> None:
            g2 = _generation(2, _owner(1000 + n), superseded=dead, death_evidence="esrch")
            queue.put((n, capture_mod.claim_generation(self.dir, INC, g2, ev)))
        procs = [ctx.Process(target=racer, args=(n,)) for n in range(16)]
        for p in procs:
            p.start()
        results = [queue.get(timeout=20) for _ in procs]
        for p in procs:
            p.join(10)
        winners = [n for n, why in results if why is None]
        losers = [why for _n, why in results if why is not None]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(set(losers), {capture_mod.OUTCOME_OWNER_CONFLICT}, results)
        highest, rec, state = capture_mod.read_generations(self.dir, INC)
        self.assertEqual((highest, state), (2, capture_mod.EVIDENCE_FINAL))
        self.assertEqual(rec["owner"]["pid"], 1000 + winners[0])
        self.assertFalse([p for p in os.listdir(self.dir) if ".tmp." in p], "a torn tmp survived")


# =====================================================================================
# L-09b -- the death rule and the terminal rule
# =====================================================================================
class L09bSuccessionRulesTests(_Dir):
    def test_fence_first_no_claim_after_publication(self) -> None:
        """C6 / iteration-3: once a fence is published, NO generation may be claimed; a later
        actor is a custodian (`fence_published_no_claim` when the owner is alive and a claim is
        attempted; plain custodian otherwise)."""
        self.assertEqual(capture_mod.may_claim_generation(fence_published=True, highest_owner_alive=True,
                                                          relinquish_record=False, death_witness="unknown"),
                         ("custodian", capture_mod.OUTCOME_FENCE_PUBLISHED_NO_CLAIM))
        self.assertEqual(capture_mod.may_claim_generation(fence_published=True, highest_owner_alive=False,
                                                          relinquish_record=False, death_witness="final"),
                         ("custodian", None))

    def test_guard_eof_with_a_live_owner_and_no_relinquish_never_claims(self) -> None:
        self.assertEqual(capture_mod.may_claim_generation(fence_published=False, highest_owner_alive=True,
                                                          relinquish_record=False, death_witness="unknown"),
                         ("refuse", capture_mod.OUTCOME_FINALIZER_ALIVE))
        self.assertEqual(capture_mod.may_claim_generation(fence_published=False, highest_owner_alive=None,
                                                          relinquish_record=False, death_witness="present"),
                         ("refuse", capture_mod.OUTCOME_SUCCESSION_UNWITNESSED))
        self.assertEqual(capture_mod.may_claim_generation(fence_published=False, highest_owner_alive=None,
                                                          relinquish_record=False, death_witness="unreadable"),
                         ("refuse", "identity_unreadable"))

    def test_a_relinquish_record_or_a_witnessed_death_authorises_the_claim(self) -> None:
        self.assertEqual(capture_mod.may_claim_generation(fence_published=False, highest_owner_alive=True,
                                                          relinquish_record=True, death_witness="unknown"),
                         ("claim", None))
        self.assertEqual(capture_mod.may_claim_generation(fence_published=False, highest_owner_alive=False,
                                                          relinquish_record=False, death_witness="final"),
                         ("claim", None))

    def test_the_relinquish_record_is_durable_and_bound_to_its_generation(self) -> None:
        self.assertTrue(capture_mod.write_relinquish(self.dir, INC, fence=FENCE, generation=1,
                                                     owner=_owner(100), reason="release",
                                                     written_at="t"))
        self.assertEqual(capture_mod.read_relinquish(self.dir, INC, 1)["outcome"], "present")
        self.assertEqual(capture_mod.read_relinquish(self.dir, INC, 2)["outcome"], "absent")
        self.assertEqual(capture_mod.read_relinquish(self.dir, "other", 1)["outcome"], "absent")

    def test_the_release_path_relinquishes_a_claimed_generation_it_will_not_finish(self) -> None:
        """A supervisor that RELEASES after claiming g1 but before any boundary exists leaves
        a durable relinquishment beside its generation, so a successor may claim g2 without a
        death witness (the supervisor is alive)."""
        session, _sentinel = spawn_session(self.room, "sleep 30\n", run_id="relq",
                                           pump_until_sentinel=False)
        owner = session._self_identity(capture_mod.OWNER_SUPERVISOR)
        g1 = capture_mod.make_owner_generation(
            fence=session.fence, generation=1, owner_role=capture_mod.OWNER_SUPERVISOR, owner=owner,
            claim_reason="test", superseded=None, death_evidence="", claimed_at="t")
        self.assertIsNone(capture_mod.claim_generation(session._owner_dir(), session.incarnation, g1,
                                                       _evidence(0, None)))
        session.release()
        rel = capture_mod.read_relinquish(session._owner_dir(), session.incarnation, 1)
        self.assertEqual(rel["outcome"], "present", rel)
        self.assertEqual(int(rel["record"]["owner"]["pid"]), os.getpid())


# =====================================================================================
# C1 / C2 / C7 -- the successor's view of a watcher that died early
# =====================================================================================
class SuccessorCutsTests(_Dir):
    def _masterless(self, run_id: str):
        profile = sh_profile(str(self.dir), drain_ms=200)
        session = rt.StandaloneSession(
            intent={"intent_id": f"i-{run_id}", "run_id": run_id, "role": "WORKER"},
            profile=profile, artifact_base=self.dir / "art", run_id=run_id,
            journal=journal_mod.ExecutionJournal(self.dir / "art", run_id))
        session.pty = None
        session.record = {"pid": 4242, "pgid": 4242, "proc_start_ticks": 7, "boot_id": "b"}   # F-005 axes
        session.capture = capture_mod.BoundedCapture(self.dir / "art" / f"{run_id}.capture.log")
        session.capture.append(b'{"type":"result","is_error":false}\n', at="t")
        return session

    def test_c1_watcher_dead_before_the_marker_is_boundary_unproven(self) -> None:
        """C1: no marker in the stream, exit proven by the ladder/table -> `boundary_unproven`."""
        session = self._masterless("c1")
        session.exit_proof = {"proven": True, "how": "ladder"}
        drained = session.drain_after_exit(budget_ms=200)
        self.assertEqual(drained["outcome"], capture_mod.OUTCOME_BOUNDARY_UNPROVEN, drained)
        self.assertFalse(rt._stream_is_final(drained))

    def test_c1_without_an_exit_proof_is_exit_unproven(self) -> None:
        session = self._masterless("c1u")
        session.capture.append(capture_mod.marker_bytes(session.fence_nonce), at="t")
        drained = session.drain_after_exit(budget_ms=200)
        self.assertEqual(drained["outcome"], "exit_unproven", drained)

    def test_c2_marker_captured_no_sentinel_publishes_with_an_unknown_exit_code(self) -> None:
        """C2: the marker is in the stream, no sentinel; the exit is proven by the ladder ->
        the successor publishes `exit.how=ladder, code=None`; `_stream_is_final` holds, and the
        settlement can only be `unknown` -- never COMPLETED (locked by the verdict)."""
        session = self._masterless("c2")
        session.capture.append(capture_mod.marker_bytes(session.fence_nonce), at="t")
        session.exit_proof = {"proven": True, "how": "ladder"}
        drained = session.drain_after_exit(budget_ms=200)
        self.assertTrue(rt._stream_is_final(drained), drained)
        fence = capture_mod.read_capture_fence(session._fence_path(), fence=session.fence)
        self.assertEqual((fence["record"]["exit"]["how"], fence["record"]["exit"]["code"]),
                         ("ladder", None))
        self.assertEqual(fence["record"]["owner"]["owner_role"], capture_mod.OWNER_SUCCESSOR)

    def test_c7_a_live_highest_owner_blocks_the_successor_by_name(self) -> None:
        session = self._masterless("c7")
        session.capture.append(capture_mod.marker_bytes(session.fence_nonce), at="t")
        session.exit_proof = {"proven": True, "how": "ladder"}
        alive = _owner(os.getpid(), pty_supervisor.proc_start_ticks(os.getpid()))
        self.assertIsNone(capture_mod.claim_generation(
            session._owner_dir(), session.incarnation,
            capture_mod.make_owner_generation(fence=session.fence, generation=1,
                                              owner_role=capture_mod.OWNER_SUPERVISOR, owner=alive,
                                              claim_reason="t", superseded=None, death_evidence="",
                                              claimed_at="t"), _evidence(0, None)))
        drained = session.drain_after_exit(budget_ms=200)
        self.assertEqual(drained["outcome"], capture_mod.OUTCOME_FINALIZER_ALIVE, drained)
        self.assertFalse(rt._stream_is_final(drained))

    def test_c7_a_dead_highest_owner_is_superseded_with_its_identity_pinned(self) -> None:
        session = self._masterless("c7d")
        session.capture.append(capture_mod.marker_bytes(session.fence_nonce), at="t")
        session.exit_proof = {"proven": True, "how": "ladder"}
        dead = _owner(4_000_000, 12345)                      # no such pid: ESRCH -> absent
        self.assertIsNone(capture_mod.claim_generation(
            session._owner_dir(), session.incarnation,
            capture_mod.make_owner_generation(fence=session.fence, generation=1,
                                              owner_role=capture_mod.OWNER_SUPERVISOR, owner=dead,
                                              claim_reason="t", superseded=None, death_evidence="",
                                              claimed_at="t"), _evidence(0, None)))
        drained = session.drain_after_exit(budget_ms=200)
        self.assertTrue(rt._stream_is_final(drained), drained)
        highest, rec, _state = capture_mod.read_generations(session._owner_dir(), session.incarnation)
        self.assertEqual(highest, 2)
        self.assertEqual(rec["superseded"]["pid"], 4_000_000)
        self.assertEqual(rec["death_evidence"], "esrch_or_start_identity_mismatch")

    def test_an_unreadable_owner_identity_refuses_the_successor(self) -> None:
        """EPERM-shaped read of the highest owner (a live process we cannot read): no claim."""
        session = self._masterless("c7u")
        session.capture.append(capture_mod.marker_bytes(session.fence_nonce), at="t")
        session.exit_proof = {"proven": True, "how": "ladder"}
        self.assertIsNone(capture_mod.claim_generation(
            session._owner_dir(), session.incarnation,
            capture_mod.make_owner_generation(fence=session.fence, generation=1,
                                              owner_role=capture_mod.OWNER_SUPERVISOR, owner=_owner(1, 7),
                                              claim_reason="t", superseded=None, death_evidence="",
                                              claimed_at="t"), _evidence(0, None)))
        session._identity_reader = lambda pid: {"start_id": 0, "start_state": "unreadable", "boot_id": "b"}
        drained = session.drain_after_exit(budget_ms=200)
        self.assertEqual(drained["outcome"], "identity_unreadable", drained)
        self.assertFalse(rt._stream_is_final(drained))


class WatcherOrphanCutTests(_Dir):
    def test_c4_the_orphan_watcher_claims_g1_only_with_a_witnessed_death(self) -> None:
        """C4 over the production spawn: the supervisor (this process) is ALIVE and merely
        closes the guard (relinquishment-or-death, unresolved) -- the watcher must NOT claim;
        its durable orphan note names `succession_unwitnessed`, no generation, no fence."""
        session, sentinel = spawn_session(self.room, sid_record("result", is_error=False)
                                          + "exit 0\n", run_id="c4", pump_until_sentinel=False)
        os.close(int(session.pty["orphan_guard_fd"]))        # guard EOF with a LIVE supervisor
        session.pty["orphan_guard_fd"] = -1
        deadline = time.time() + 15
        note = Path(os.fsdecode(pty_supervisor.orphan_note_path(session.capture.path, session.incarnation)))
        while not note.exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(note.exists(), "the orphan watcher wrote no note")
        recorded = json.loads(note.read_text())
        self.assertEqual(recorded["outcome"], capture_mod.OUTCOME_SUCCESSION_UNWITNESSED, recorded)
        self.assertEqual(capture_mod.read_generations(session._owner_dir(), session.incarnation)[0], 0)
        self.assertEqual(capture_mod.read_capture_fence(session._fence_path(), fence=session.fence)["outcome"],
                         "absent")


if __name__ == "__main__":
    unittest.main()
