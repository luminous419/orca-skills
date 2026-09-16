"""OS-37 BUGFIX iteration 2 (run_829ca55e36c9): the two LOCK-coverage corrections the
iteration-1 reviewer required (`REVIEW_BUGFIX.md` §11 findings 1 and 2).  The item 1–6
IMPLEMENTATION was judged sound; these add the missing red-at-`fc21012` / green-here locks.

Finding 1 — the real-PTY supervisor crash-cut.  A PRODUCTION supervisor
(`build_standalone_adapter` -> `execute_state` -> `adapter.start` -> `StandaloneSession`)
runs a real dispatch on a real POSIX pty with a real forked exit watcher; the supervisor is
SIGKILLed (a real kill of a real separate process) at the deterministic cut BETWEEN the
watcher's fenced exit sentinel and the supervisor's own finalized proof — caught there by a
test-only barrier the supervisor subprocess installs on `StandaloneSession._finalize_drain`
(NO production line changes; the watcher, a forked child, never calls that method).  The
adopted successor (the production watchdog `recover`) then REFUSES with `stream_end_unproven`
rather than settling from the capture the sentinel alone would have vouched for — no
COMPLETED state, no settlement-success row, no success receipt.  The positive twin lets the
supervisor write its proof first, then kills before it settles, and the adopted successor
settles COMPLETED from that supervisor-written proof.  At `fc21012`, where the sentinel alone
was stream-final, the negative cut settles COMPLETED — so the lock is RED there.

Finding 2 — item 5's path matrix.  A same-digest-name profile archive that is (a) wrong
digest, (b) schema-invalid or (c) an unfrozen (relative / omitted worktree) path is driven
through BOTH `persist_standalone_profile` (the normal write path) AND
`migrate_standalone_profile` (before the authority rebind): a typed refusal by name, no
authority rebind, no `committed` migration record, and the pre-placed archive byte-untouched.
At `fc21012`, `persist_standalone_profile` skipped an existing archive by filename, so the
corrupt archive was published / rebound to — the locks are RED there.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from typing import Any

from scripts.deterministic_workflow import (launcher,  # noqa: E402
                                            standalone_capture as capture_mod,
                                            standalone_journal as journal_mod,
                                            standalone_pty as pty_supervisor)
from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore  # noqa: E402
from scripts.test_os37_followup_review_regressions import (  # noqa: E402
    LANGGRAPH_REASON, _langgraph_ok, agent_profile_spec, stub_profile_spec)
from scripts.test_os37_recovery_boundary_regressions import (  # noqa: E402
    _CrashRoom, _wait_until, pid_alive)

REPO = Path(__file__).resolve().parent.parent


# =====================================================================================
# Finding 1 — the real-PTY supervisor crash-cut, between the sentinel and the proof
# =====================================================================================
# The supervisor subprocess installs a TEST-ONLY barrier on
# `StandaloneSession._finalize_drain` (the supervising session's proof writer).  It is
# NOT a production seam: the forked exit watcher never calls `_finalize_drain` (it writes
# its own proof through the module-level `_finalize_orphaned_capture`), so this monkeypatch
# in the supervisor process cannot change the watcher's behaviour.  The barrier lets the
# parent stop the supervisor deterministically at one of two cut points:
#   * `finalize_before` — reached-marker, block, THEN the original: killed while blocked,
#     the proof was never written (the NEGATIVE cut);
#   * `finalize_after`  — the original (which writes the proof), reached-marker, block:
#     killed while blocked, the proof exists but the dispatch is not yet settled (POSITIVE).
_BARRIER_SUPERVISOR = textwrap.dedent("""
    import json, os, sys, time
    sys.path.insert(0, sys.argv[1])
    (base, run_id, ledger_path, profile_path, barrier_dir, mode) = sys.argv[2:8]
    from pathlib import Path
    from scripts.deterministic_workflow import launcher, recovery_store
    from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
    from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
    reached = Path(barrier_dir) / "reached"
    release = Path(barrier_dir) / "release"
    _orig = StandaloneSession._finalize_drain
    _fired = {"n": 0}
    def _barrier(self, drained):
        if _fired["n"]:
            return _orig(self, drained)
        _fired["n"] = 1
        if mode == "finalize_after":
            out = _orig(self, drained)               # writes the proof FIRST
            reached.write_text("1")
            deadline = time.time() + 120
            while not release.exists() and time.time() < deadline:
                time.sleep(0.02)
            return out
        # finalize_before: reached, block, THEN the original (never reached if killed)
        reached.write_text("1")
        deadline = time.time() + 120
        while not release.exists() and time.time() < deadline:
            time.sleep(0.02)
        return _orig(self, drained)
    StandaloneSession._finalize_drain = _barrier
    lease = 3.0
    ledger = FileRuntimeStateStore(Path(ledger_path), lease_seconds=lease)
    profile = json.loads(Path(profile_path).read_text())
    adapter, state = launcher.build_standalone_adapter(
        {"run_id": run_id, "thread_id": "t", "phases": ["DESIGN"], "max_iterations": 2},
        artifact_base=Path(base), run_id=run_id, runtime_state=ledger, profile_spec=profile)
    checkpoint = launcher.resolve_checkpoint_path(run_id, "t", artifact_base=Path(base))
    authority = recovery_store.FileRecoveryStateStore(
        recovery_store.authority_path_for_checkpoint(checkpoint), lease_seconds=lease)
    final = launcher.execute_state(
        state, adapter=adapter, runtime_state=ledger,
        journal=launcher._standalone_pause_row_journal(Path(base), run_id),
        artifact_base=Path(base), audit_sink=None, execution_authority=authority)
    print(json.dumps({"terminal_status": final.get("terminal_status")}))
""")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class Finding1SupervisorCrashCutTests(_CrashRoom):
    """[P1] The production supervisor is SIGKILLed between the fenced exit sentinel and its
    own finalized proof; the adopted successor refuses (`stream_end_unproven`) rather than
    settling from the capture the sentinel alone would have vouched for.  Red at `fc21012`
    (sentinel alone was stream-final -> COMPLETED); green here."""

    def _profile(self) -> dict:
        # A short Worker turn: the barrier, not the turn length, controls the cut.
        return agent_profile_spec(
            worktree=str(self.base / "worktree"),
            driver_env={"OS37_GA_TURN_DELAY_MS": "200",
                        "OS37_GA_TURN_DELAY_ROLE": "WORKER"},
            timeouts={"completion_timeout_ms": 30000})

    def _launch_barriered(self, run_id: str, mode: str) -> Path:
        ledger_path = launcher.default_runtime_state_path(run_id, "t")
        profile_path = self.base / f"{run_id}.profile.json"
        profile_path.write_text(json.dumps(self._profile()))
        barrier_dir = self.base / f"{run_id}.barrier"
        barrier_dir.mkdir()
        script = self.base / f"{run_id}.supervisor.py"
        script.write_text(_BARRIER_SUPERVISOR)
        self.supervisor = subprocess.Popen(
            [sys.executable, str(script), str(REPO), str(self.base), run_id,
             str(ledger_path), str(profile_path), str(barrier_dir), mode],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ))
        return barrier_dir

    def _worker_spawn(self, run_id: str) -> dict:
        """Block until the Worker's spawn row exists; return it (session_id/incarnation)."""
        journal = self.journal(run_id)

        def spawned() -> bool:
            if not journal.path.exists():
                return False
            return any(r["event"] == "spawned" for r in journal.rows()
                       if r.get("kind") == "EVENT")
        _wait_until(spawned, timeout=60, what="the Worker's spawn row")
        row = next(r for r in journal.rows()
                   if r.get("kind") == "EVENT" and r["event"] == "spawned")
        self.agent_pids.append(int(row["source_vocabulary"]["pid"]))
        return row

    def _sentinel_and_proof(self, run_id: str, spawned: dict) -> tuple[Path, Path]:
        sess = spawned["session_id"]
        inc = spawned["process_incarnation"]
        sentinel = pty_supervisor.exit_sentinel_path(self.base, run_id, sess, inc)
        proof = Path(os.fsdecode(capture_mod.capture_finalized_path(
            capture_mod.capture_path(self.base, run_id, sess), inc)))
        return sentinel, proof

    def _settlements(self, run_id: str, intent_id: str) -> list[dict]:
        return [r for r in self.journal(run_id).rows_for(intent_id)
                if r["kind"] == "SETTLEMENT_OBSERVED"]

    def _worker_intent_id(self, run_id: str) -> str:
        for r in self.journal(run_id).rows():
            if r.get("event") == "spawned" and r.get("kind") == "EVENT":
                return str(r["intent_id"])
        raise AssertionError("no worker intent")

    def test_a_kill_between_sentinel_and_proof_refuses_stream_end_unproven(self) -> None:
        run_id = "run_cutneg"
        barrier = self._launch_barriered(run_id, "finalize_before")
        spawned = self._worker_spawn(run_id)
        intent_id = str(spawned["intent_id"])
        sentinel, proof = self._sentinel_and_proof(run_id, spawned)
        # The barrier is reached only after the drain; the watcher has written the sentinel
        # and exited (it does not orphan-finalize once the agent is reaped), and the proof
        # is NOT yet written.
        _wait_until(lambda: (barrier / "reached").exists() and sentinel.exists(),
                    timeout=60, what="the sentinel + the pre-proof barrier")
        self.assertFalse(proof.exists(),
                         "the supervisor wrote its finalized proof before the cut")
        self.assertEqual(pty_supervisor.read_exit_sentinel(sentinel, fence=f"{spawned['session_id']}:{spawned['process_incarnation']}")["outcome"],
                         "exited", "the watcher had not written the sentinel at the cut")
        # THE CRASH: a real SIGKILL of the real supervisor process, blocked before its proof.
        self.kill_supervisor()
        time.sleep(self.LEASE_SECONDS + 0.5)
        # No proof exists and no lingering watcher will write one (the watcher exited after
        # the sentinel), so the adopted successor must refuse.
        self.assertFalse(proof.exists(), "a finalized proof appeared after the crash")
        code, summary, stderr, escaped = self.recover(run_id)
        self.assertIsNone(escaped, f"the recovery escaped: {escaped!r}")
        settled = self._settlements(run_id, intent_id)
        self.assertEqual(len(settled), 1, f"{summary!r}\n{stderr}")
        self.assertEqual(settled[0]["state"], "FAILED",
                         "the adopted successor settled from the unproven stream")
        self.assertNotEqual(settled[0]["outcome"], "succeeded")
        verdict = (settled[0]["source_vocabulary"] or {}).get("completion_verdict") or {}
        self.assertEqual(verdict.get("reason"), "stream_end_unproven",
                         settled[0]["source_vocabulary"])
        # No success receipt / settlement in the ledger.
        ledger = FileRuntimeStateStore(launcher.default_runtime_state_path(run_id, "t"))
        ledger_settlement = ledger.get_settlement(intent_id)
        self.assertTrue(ledger_settlement is None
                        or ledger_settlement.get("status") != "succeeded",
                        ledger_settlement)
        head = None
        with contextlib.suppress(Exception):
            from scripts.deterministic_workflow import recovery_runtime
            head = recovery_runtime.resolve_head(run_id, artifact_base=self.base)
        if head is not None:
            self.assertNotEqual(head.state.get("terminal_status"), "COMPLETED",
                                "the run reached COMPLETED over an unproven stream")

    def test_the_positive_twin_settles_from_the_supervisor_written_proof(self) -> None:
        run_id = "run_cutpos"
        barrier = self._launch_barriered(run_id, "finalize_after")
        spawned = self._worker_spawn(run_id)
        intent_id = str(spawned["intent_id"])
        sentinel, proof = self._sentinel_and_proof(run_id, spawned)
        # The barrier is reached AFTER `_finalize_drain` wrote the proof; kill before the
        # supervisor settles, so the ADOPTED successor is the one that settles.
        _wait_until(lambda: (barrier / "reached").exists() and proof.exists(),
                    timeout=60, what="the supervisor-written finalized proof")
        record = capture_mod.read_capture_finalized(
            os.fsencode(str(proof)),
            fence=f"{spawned['session_id']}:{spawned['process_incarnation']}")
        self.assertEqual(record["outcome"], capture_mod.FINALITY_PROVEN, record)
        self.assertEqual(record["record"]["writer"], capture_mod.WRITER_SUPERVISOR)
        self.kill_supervisor()
        time.sleep(self.LEASE_SECONDS + 0.5)
        code, summary, stderr, escaped = self.recover(run_id)
        self.assertIsNone(escaped, f"the recovery escaped: {escaped!r}")
        settled = self._settlements(run_id, intent_id)
        self.assertEqual(len(settled), 1, f"{summary!r}\n{stderr}")
        self.assertEqual(settled[0]["state"], "COMPLETED",
                         "the adopted successor did not honour the supervisor's proof")

    def test_the_watcher_finalized_path_never_leaves_a_sentinel_without_a_proof(self) -> None:
        """The orphaned (watcher-finalizes) path writes the proof BEFORE the sentinel, so a
        watcher-written sentinel always implies a matching proof — there is no
        'between sentinel and proof' window for the watcher to be killed in.  A real full
        `_watch` over a real pty, driven through the production spawn, is the authority."""
        room = Path(tempfile.mkdtemp(prefix="os37-r9i2-watch-"))
        self.addCleanup(shutil.rmtree, room, True)
        agent = room / "agent.sh"
        agent.write_text(textwrap.dedent("""\
            #!/bin/sh
            echo '{"type":"system","session_id":"x"}'
            echo '{"type":"result","is_error":false}'
            sleep 2
            exit 0
        """))
        agent.chmod(0o755)
        supervisor = room / "sup.py"
        supervisor.write_text(textwrap.dedent("""
            import json, os, sys, time
            sys.path.insert(0, sys.argv[1])
            from scripts.deterministic_workflow import standalone_pty as pty
            from scripts.deterministic_workflow.standalone_profile import profile_from_mapping
            room, agent, handoff = sys.argv[2], sys.argv[3], sys.argv[4]
            profile = profile_from_mapping({
                "driver": "claude", "binary": "sh", "supported_range": [[1,0,0],[9,0,0]],
                "bin_dirs": ["/bin"], "worktree": room,
                "readiness_records": [{"channel":"structured","record_type":"system",
                                       "session_field":"session_id"}],
                "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
                "identity_flag": "--session-id"})
            base = os.path.join(room, "artifacts")
            sent = pty.exit_sentinel_path(base, "run_w", "sess", "inc1")
            os.makedirs(os.path.dirname(sent), exist_ok=True)
            cap = os.path.join(os.path.dirname(sent), "capture.log")
            s = pty.spawn(argv=["/bin/sh", agent], env={"PATH":"/bin:/usr/bin"},
                profile=profile, session_id="sess", incarnation="inc1",
                spawn_record_target=pty.spawn_record_path(base,"run_w","i","inc1"),
                cwd=room, sentinel=str(sent), fence="sess:inc1", image="/bin/sh",
                capture=cap)
            json.dump({"leader": s["leader_pid"], "agent": s["pid"],
                       "sentinel": str(sent), "capture": cap}, open(handoff,"w"))
            os._exit(0)                      # supervisor dies at once: the watcher orphans
        """))
        handoff = room / "h.json"
        done = subprocess.run([sys.executable, str(supervisor), str(REPO), str(room),
                               str(agent), str(handoff)], capture_output=True, text=True,
                              timeout=60, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        info = json.loads(handoff.read_text())

        def _reap() -> None:
            for p in (info["agent"], info["leader"]):
                with contextlib.suppress(OSError):
                    os.kill(int(p), signal.SIGKILL)
        self.addCleanup(_reap)
        sentinel = Path(info["sentinel"])
        proof = Path(os.fsdecode(capture_mod.capture_finalized_path(
            os.fsencode(info["capture"]), "inc1")))
        # The watcher orphan-drains, writes the PROOF, then the sentinel — so whenever the
        # sentinel exists the proof exists too.
        _wait_until(sentinel.exists, timeout=30, what="the watcher's sentinel")
        self.assertTrue(proof.exists(),
                        "the watcher wrote a sentinel with no finalized proof beside it")
        record = capture_mod.read_capture_finalized(os.fsencode(str(proof)),
                                                    fence="sess:inc1")
        self.assertIn(record["outcome"],
                      (capture_mod.FINALITY_PROVEN, capture_mod.FINALITY_UNPROVEN),
                      record)


# =====================================================================================
# Finding 2 — item 5's corrupt-archive matrix across BOTH doors
# =====================================================================================
class Finding2ArchiveValidationMatrixTests(unittest.TestCase):
    """A same-digest-name archive that is wrong-digest / schema-invalid / unfrozen is
    refused by name on BOTH the normal write path (`persist_standalone_profile`) AND the
    migration path (`migrate_standalone_profile`), with no authority rebind, no `committed`
    record, and the pre-placed archive byte-untouched.  Red at `fc21012` (an existing
    archive was skipped by filename, so the corrupt bytes were published / rebound to)."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r9i2-arch-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt").mkdir()
        (self.base / "wt2").mkdir()
        self.good = launcher.freeze_profile_worktree(
            stub_profile_spec("alive", worktree=str(self.base / "wt")))
        self.good_b = launcher.freeze_profile_worktree(
            stub_profile_spec("alive", worktree=str(self.base / "wt2")))

    # -- the three corruptions, each returning (digest, pre_placed_bytes) ----------------
    def _wrong_digest(self, spec: dict) -> tuple[str, str]:
        digest = launcher.profile_digest(spec)             # the digest the op targets
        return digest, '{"driver":"claude","binary":"not-this-profile"}\n'  # ≠ digest

    def _schema_invalid(self, spec: dict) -> tuple[str, str]:
        bad = dict(spec)
        bad["supported_range"] = "not-a-range"             # hashes to its OWN digest
        return launcher.profile_digest(bad), launcher.profile_payload(bad) + "\n"

    def _unfrozen(self, spec: dict) -> tuple[str, str]:
        rel = dict(spec)
        rel["worktree"] = "relative/wt"                    # a pre-fix, unfrozen archive
        return launcher.profile_digest(rel), launcher.profile_payload(rel) + "\n"

    def _preplace(self, run_id: str, digest: str, payload: str) -> Path:
        archive = launcher.profile_archive_path(self.base, run_id, digest)
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(payload)
        return archive

    # -- door A: persist_standalone_profile ---------------------------------------------
    def _persist_case(self, run_id: str, spec: dict, digest: str, payload: str) -> None:
        archive = self._preplace(run_id, digest, payload)
        before = archive.read_bytes()
        with self.assertRaises(launcher.LauncherError) as caught:
            # For wrong-digest the incoming spec is the GOOD one targeting `digest`; for the
            # schema/unfrozen cells the incoming spec IS the corrupt one (it hashes to the
            # pre-placed archive), so the door validates the archive it would publish.
            launcher.persist_standalone_profile(self.base, run_id, spec)
        msg = str(caught.exception)
        self.assertTrue(any(code in msg for code in (
            launcher.STANDALONE_PROFILE_ARCHIVE_INVALID,
            launcher.STANDALONE_PROFILE_WORKTREE_UNFROZEN,
            launcher.STANDALONE_PROFILE_WORKTREE_INVALID)), msg)
        # The pre-placed archive is byte-untouched and no current profile.json was published.
        self.assertEqual(archive.read_bytes(), before, "the corrupt archive was rewritten")
        self.assertFalse(launcher.standalone_profile_path(self.base, run_id).exists(),
                         "a current profile.json was published over the refusal")

    def test_persist_refuses_wrong_digest_archive(self) -> None:
        digest, payload = self._wrong_digest(self.good)
        self._persist_case("run_pw", self.good, digest, payload)

    def test_persist_refuses_schema_invalid_archive(self) -> None:
        bad = dict(self.good); bad["supported_range"] = "not-a-range"
        digest, payload = self._schema_invalid(self.good)
        self._persist_case("run_ps", bad, digest, payload)

    def test_persist_refuses_unfrozen_archive(self) -> None:
        rel = dict(self.good); rel["worktree"] = "relative/wt"
        digest, payload = self._unfrozen(self.good)
        self._persist_case("run_pu", rel, digest, payload)

    # -- door B: migrate_standalone_profile (before the authority rebind) ----------------
    def _launch(self, run_id: str) -> None:
        launcher.publish_standalone_launch_bindings(
            self.base, run_id, profile_spec=self.good,
            runtime_state_path=(self.base / f"{run_id}.ledger.json").resolve(),
            thread_id="t")

    def _migrate_case(self, run_id: str, new_spec: dict, digest: str, payload: str) -> None:
        self._launch(run_id)
        old_digest = launcher.load_standalone_authority(self.base, run_id, "t")["profile_digest"]
        archive = self._preplace(run_id, digest, payload)
        before = archive.read_bytes()
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.migrate_standalone_profile(
                self.base, run_id, thread_id="t", new_profile_spec=new_spec,
                actor="alice", reason="retune")
        msg = str(caught.exception)
        self.assertTrue(any(code in msg for code in (
            launcher.STANDALONE_PROFILE_ARCHIVE_INVALID,
            launcher.STANDALONE_MIGRATION_REFUSED,
            launcher.STANDALONE_PROFILE_WORKTREE_UNFROZEN,
            launcher.STANDALONE_PROFILE_WORKTREE_INVALID)), msg)
        # No authority rebind: the bound digest is unchanged.
        self.assertEqual(
            launcher.load_standalone_authority(self.base, run_id, "t")["profile_digest"],
            old_digest, "the authority was rebound over a corrupt archive")
        # No committed migration record.
        self.assertEqual(launcher.standalone_committed_migrations(self.base, run_id, "t"), (),
                         "a migration committed over a corrupt archive")
        # The pre-placed archive is byte-untouched.
        self.assertEqual(archive.read_bytes(), before, "the corrupt archive was rewritten")

    def test_migrate_refuses_wrong_digest_archive(self) -> None:
        # A valid NEW profile targeting a digest whose archive is corrupt (wrong content).
        digest = launcher.profile_digest(self.good_b)
        self._preplace("run_mw", digest, '{"driver":"claude","binary":"wrong"}\n')
        self._migrate_case("run_mw", self.good_b, digest,
                           '{"driver":"claude","binary":"wrong"}\n')

    def test_migrate_refuses_schema_invalid_new_profile_before_any_write(self) -> None:
        # A schema-invalid migration target is refused before any durable write; assert the
        # invariants (no rebind, no committed, archive untouched) hold.
        bad = dict(self.good_b); bad["supported_range"] = "not-a-range"
        digest, payload = self._schema_invalid(self.good_b)
        self._migrate_case("run_ms", bad, digest, payload)

    def test_migrate_freezes_an_unfrozen_target_then_refuses_the_corrupt_archive(self) -> None:
        # An unfrozen migration target is FROZEN at the migrate door, so the pre-placed
        # unfrozen archive (a different, wrong-digest file) is the wrong-digest refusal.
        rel = dict(self.good_b); rel["worktree"] = "relative/wt"
        frozen_digest = launcher.profile_digest(launcher.freeze_profile_worktree(rel))
        _digest, payload = self._unfrozen(self.good_b)     # the unfrozen archive bytes
        self._preplace("run_mu", frozen_digest, payload)   # placed at the frozen target
        self._migrate_case("run_mu", rel, frozen_digest, payload)

    def test_the_validation_function_refuses_each_corruption_by_name(self) -> None:
        # The item-5 validator itself, over an archive that HASHES to its own digest: the
        # schema and unfrozen refusals are reached (a wrong-digest file never hashes).
        run_id = "run_val"
        # schema-invalid, self-consistent digest
        d1, p1 = self._schema_invalid(self.good)
        self._preplace(run_id, d1, p1)
        with self.assertRaises(launcher.LauncherError):
            launcher._validated_profile_archive(self.base, run_id, d1)
        # unfrozen, self-consistent digest
        d2, p2 = self._unfrozen(self.good)
        self._preplace(run_id, d2, p2)
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher._validated_profile_archive(self.base, run_id, d2)
        self.assertIn(launcher.STANDALONE_PROFILE_WORKTREE_UNFROZEN, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
