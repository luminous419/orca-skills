"""OS-37 BUGFIX (run_829ca55e36c9): one behaviour-based lock per merge blocker of the
consolidated external review of head `fc21012` (issuecomment-5680361023).

VERSIONED BY OS-48 (run_f820764749d6, W-F9): item 1's `os37.capture_finalized.v1` proof
(whole-file length + digest after a quiet drain; `stream_end_unproven`) is retired.  The
OS-48 proof is the FENCE `os48.capture_fence.v1` -- a positive boundary N at the in-band
marker with sha256(capture[0,N)) -- and its named non-success is `boundary_unproven`.  The
item-1 classes below are rewritten over the fence; items 2-6 are unaffected.

Each test FAILS at `fc21012` and passes after the fix, and each exercises PRODUCTION
wiring -- `standalone_capture`'s finalized-proof contract over a REAL pty, the exit
watcher's forked drain, `StandaloneSession.await_completion` / `drain_after_exit`, the
launcher's authority read / upgrade / migration path, the watchdog sweep -- and reads
DURABLE / OS-level state.

  1. CAPTURE-FINALIZED proof, DISTINCT from the exit sentinel.  The sentinel proves the
     PROCESS exited; a separate fenced, crash-durable finalized record proves the CAPTURE
     is complete, written only after the finalizing writer's bounded drain + meta fsync,
     bound to the capture's final length + sha256 + the exit identity.  Adopted recovery
     requires that proof (sentinel alone -> `stream_end_unproven`).  The finality gate is
     TOTAL (an exit first observed AT the completion deadline still runs it).
  2. CRASH-CONSISTENT prompt-composition upgrade/migration: durable `prepared` intent ->
     authority CAS/rebind -> `committed`; replay after every crash cut point converges to
     exactly one committed record for the live authority; a crash after the rebind never
     leaves the new composition bound without an audit record.
  3. PER-RUN watchdog isolation: a corrupt authority for ONE run is a typed
     unavailable/escalation for THAT run, and the fleet sweep still classifies and
     recovers the healthy runs.
  4. INVALID `worktree` TYPES are rejected: only a missing key or the exact `""` means the
     launch cwd; `123` / `[]` / `{}` / `None` are a typed refusal BEFORE freeze / digest /
     persist, at every door.
  5. An EXISTING content-addressed profile archive is VALIDATED (digest, frozen paths,
     schema, through the production loader) before authority is published or rebound to
     it, on the normal write path AND the migration path.
  6. A missing capture sha256 is NEVER healed: `RawBoundedAppender` requires the closed v2
     meta shape with a valid digest; an absent / empty / invalid `sha256` (or any inherited
     integrity failure) is irreversibly unanswerable -- never re-derived, never written
     back, never answerable after handoff.

  PTY scope (choice (a), fail-closed + observable): a descendant retaining the slave keeps
  the drain from proving; the finalized record is `unproven` and NAMES the holder, and the
  drain bound is the profile's `post_exit_drain_budget_ms`.

  Doc scope: `migrate-standalone-prompt-composition` supports only the omitted-thread
  legacy authority shape; the conformance doc states that scope, and an explicit-thread
  legacy record is refused rather than silently mis-handled.
"""
from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import shutil
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from scripts.deterministic_workflow import (launcher,  # noqa: E402
                                            standalone_capture as capture_mod,
                                            standalone_journal as journal_mod,
                                            standalone_pty as pty_supervisor,
                                            standalone_runtime as runtime_mod)
from scripts.deterministic_workflow.standalone_profile import (  # noqa: E402
    CaptureLimits, ProfileError, profile_from_mapping)
from scripts.test_os37_followup_review_regressions import (  # noqa: E402
    LANGGRAPH_REASON, _langgraph_ok, agent_profile_spec, stub_profile_spec)
from scripts.test_os37_recovery_boundary_regressions import (  # noqa: E402
    _CrashRoom, pid_alive)

REPO = Path(__file__).resolve().parent.parent
CONFORMANCE = REPO / "docs" / "conformance" / "OS37_CONFORMANCE.md"


# =====================================================================================
# Item 1 -- the capture FENCE (OS-48), distinct from the exit sentinel
# =====================================================================================
# superseded by OS-48: `Item1FinalizedProofContractTests` locked `os37.capture_finalized.v1`
# (a whole-file length+digest proof written after a quiet drain).  The OS-48 proof is the
# FENCE `os48.capture_fence.v1`: a positive boundary N (the in-band marker's offset) with
# sha256(capture[0,N)); bytes after N are diagnostic and never disturb it.
def _fence_record(*, fence: str, nonce: str, data: bytes, exit_how: str = "exit_sentinel",
                  exit_code: int | None = 0, **over: Any) -> dict[str, Any]:
    offset_n, marker_len, state = capture_mod.marker_span(data, nonce)
    assert state == capture_mod.EVIDENCE_FINAL, state
    args = dict(fence=fence, emitter={"pid": 1, "start_id": 1, "boot_id": "b"}, emitter_pgid=1,
                offset_n=offset_n, marker_len=marker_len, marker_nonce=nonce,
                sha256_prefix=capture_mod.prefix_digest(data, offset_n),
                tail_bytes_at_publish=len(data) - offset_n - marker_len, exit_how=exit_how,
                exit_code=exit_code, reaped_by=None,
                owner={"owner_role": capture_mod.OWNER_SUPERVISOR, "generation": 1,
                       "owner": {"pid": 2, "start_id": 2, "boot_id": "b"}},
                evidence_source="test", provenance=["test"], published_at="t")
    args.update(over)
    return capture_mod.make_capture_fence(**args)


class Item1FenceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r9-i1c-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.capture = self.base / "capture.log"
        self.nonce = "0" * 32
        self.capture.write_bytes(b'{"type":"result","subtype":"success"}\n'
                                 + capture_mod.marker_bytes(self.nonce))
        self.fence = "sess:i-1"

    def _publish(self, **over: Any) -> bytes:
        record = _fence_record(fence=self.fence, nonce=self.nonce,
                               data=self.capture.read_bytes(), **over)
        path = capture_mod.capture_fence_path(os.fsencode(str(self.capture)), "i-1")
        self.assertTrue(capture_mod.write_capture_fence(path, record))
        return path

    def test_a_final_fence_binds_the_capture_prefix_and_the_sentinel(self) -> None:
        path = self._publish()
        read = capture_mod.read_capture_fence(path, fence=self.fence)
        self.assertEqual(read["outcome"], capture_mod.EVIDENCE_FINAL)
        bound = capture_mod.fence_matches(read["record"], capture=self.capture,
                                          sentinel_code=0, sentinel_present=True)
        self.assertTrue(bound["matches"], bound)

    def test_bytes_after_the_boundary_never_disturb_the_fence(self) -> None:
        """# superseded by OS-48: `..._a_capture_that_later_grew_no_longer_matches` -- growth
        # AFTER N is the diagnostic tail (retained, never bound); a change INSIDE [0, N) is."""
        path = self._publish()
        with open(self.capture, "ab") as handle:
            handle.write(b'{"late":true}\n')                 # after N: diagnostic
        read = capture_mod.read_capture_fence(path, fence=self.fence)
        self.assertTrue(capture_mod.fence_matches(read["record"], capture=self.capture,
                                                  sentinel_code=0, sentinel_present=True)["matches"])
        data = bytearray(self.capture.read_bytes())
        data[0:1] = b"["                                        # inside [0, N)
        self.capture.write_bytes(bytes(data))
        bound = capture_mod.fence_matches(read["record"], capture=self.capture,
                                          sentinel_code=0, sentinel_present=True)
        self.assertFalse(bound["matches"])
        self.assertEqual(bound["reason"], "capture_digest_mismatch")

    def test_a_fence_that_cites_the_sentinel_needs_the_sentinel_to_agree(self) -> None:
        path = self._publish(exit_code=0)
        read = capture_mod.read_capture_fence(path, fence=self.fence)
        self.assertEqual(capture_mod.fence_matches(
            read["record"], capture=self.capture, sentinel_code=None,
            sentinel_present=False)["reason"], "sentinel_absent")
        self.assertEqual(capture_mod.fence_matches(
            read["record"], capture=self.capture, sentinel_code=7,
            sentinel_present=True)["reason"], "sentinel_code_mismatch")

    def test_a_foreign_fence_returns_no_record(self) -> None:
        path = self._publish()
        read = capture_mod.read_capture_fence(path, fence="sess:i-other")
        self.assertEqual(read["outcome"], "foreign")
        self.assertIsNone(read["record"])

    def test_the_fence_is_link_exclusive_and_never_overwritten(self) -> None:
        path = self._publish()
        before = Path(os.fsdecode(path)).read_bytes()
        second = _fence_record(fence=self.fence, nonce=self.nonce,
                               data=self.capture.read_bytes(), published_at="later")
        self.assertFalse(capture_mod.write_capture_fence(path, second))
        self.assertEqual(Path(os.fsdecode(path)).read_bytes(), before)

    def test_a_legacy_finalized_record_is_refused_by_name(self) -> None:
        legacy = capture_mod.capture_finalized_path(os.fsencode(str(self.capture)), "i-1")
        Path(os.fsdecode(legacy)).write_text(json.dumps({
            "schema": capture_mod.LEGACY_FINALIZED_SCHEMA, "fence": self.fence,
            "finality": "proven"}))
        read = capture_mod.read_capture_fence(
            capture_mod.capture_fence_path(os.fsencode(str(self.capture)), "i-1"),
            fence=self.fence, legacy_path=legacy)
        self.assertEqual(read["outcome"], capture_mod.OUTCOME_LEGACY_FINALIZED, read)
        self.assertIsNone(read["record"])


class Item1StreamFinalityIsTheProofTests(unittest.TestCase):
    """`_stream_is_final` accepts ONLY a VERIFIED FENCE (`capture_finalized`); the exit
    sentinel alone -- the round-8 rule -- is refused, and so is every OS-48 named
    non-success."""

    def test_only_the_verified_fence_is_final(self) -> None:
        f = runtime_mod._stream_is_final
        self.assertTrue(f({"finality": "capture_finalized"}))
        for other in ("exit_sentinel", "exit_sentinel_only", "none", "unproven",
                      "mismatch", "foreign", "absent", "legacy_finalized_record", ""):
            self.assertFalse(f({"finality": other}), other)
        # The round-8 shape a stale reader might build is no longer final.
        self.assertFalse(f({"ended": "no_master", "finality": "exit_sentinel"}))


class Item1MasterlessRequiresTheProofTests(unittest.TestCase):
    """An adopted (masterless) session settles finality ONLY from a matching fence.  A
    sentinel with no fence and no captured marker -- the crashed-mid-drain supervisor -- is
    `boundary_unproven` and NOT final; a legacy finalized record is refused BY NAME."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r9-i1m-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        profile = profile_from_mapping(stub_profile_spec("alive", worktree=str(self.base),
                                                         timeouts={"post_exit_drain_budget_ms": 200}))
        self.session = runtime_mod.StandaloneSession(
            intent={"intent_id": "i-m", "run_id": "run_m", "role": "WORKER"},
            profile=profile, artifact_base=self.base, run_id="run_m",
            journal=journal_mod.ExecutionJournal(self.base, "run_m"))
        self.session.pty = None                            # adopted: no master
        # F-005: an adopted session's record carries the pinned emitter identity axes
        # (bound from the spawn record in production); a successor publishes nothing without them.
        self.session.record = {"pid": 4242, "pgid": 4242, "proc_start_ticks": 7, "boot_id": "b"}
        self.session.capture = capture_mod.BoundedCapture(self.base / "capture.log")
        self.session.capture.append(b'{"type":"result","subtype":"success"}\n', at="t")

    def _sentinel(self) -> Path:
        s = pty_supervisor.exit_sentinel_path(self.base, "run_m", self.session.session_id,
                                              self.session.incarnation)
        s.parent.mkdir(parents=True, exist_ok=True)
        pty_supervisor.write_exit_sentinel(s, code=0, fence=self.session.fence)
        return s

    def _marker(self) -> None:
        self.session.capture.append(capture_mod.marker_bytes(self.session.fence_nonce), at="t")

    def test_a_sentinel_with_no_fence_and_no_marker_is_not_final(self) -> None:
        self._sentinel()
        drained = self.session.drain_after_exit(budget_ms=200)
        self.assertEqual(drained["finality"], "none", drained)
        self.assertEqual(drained["outcome"], capture_mod.OUTCOME_BOUNDARY_UNPROVEN, drained)
        self.assertFalse(runtime_mod._stream_is_final(drained))

    def test_the_matching_fence_is_final(self) -> None:
        self._sentinel()
        self._marker()
        cap = self.session.capture
        capture_mod.write_capture_fence(
            self.session._fence_path(),
            _fence_record(fence=self.session.fence, nonce=self.session.fence_nonce,
                          data=cap.raw()))
        drained = self.session.drain_after_exit(budget_ms=200)
        self.assertEqual(drained["finality"], "capture_finalized", drained)
        self.assertTrue(runtime_mod._stream_is_final(drained))
        self.assertEqual(int(drained["offset_n"]), cap.raw().index(b"\n<<OS48-FENCE"))

    def test_a_captured_marker_with_no_fence_lets_the_successor_publish(self) -> None:
        """C7 shape: the sentinel AND the marker are on disk but the owner died before
        publishing (no generation at all): the successor claims g1 and publishes from the
        captured marker; the fence then binds and is final."""
        self._sentinel()
        self._marker()
        drained = self.session.drain_after_exit(budget_ms=200)
        self.assertEqual(drained["finality"], "capture_finalized", drained)
        fence = capture_mod.read_capture_fence(self.session._fence_path(), fence=self.session.fence)
        self.assertEqual(fence["record"]["owner"]["owner_role"], capture_mod.OWNER_SUCCESSOR)
        self.assertIn("published_by_successor_from_captured_marker", fence["record"]["provenance"])

    def test_a_fence_that_does_not_bind_the_capture_is_a_named_mismatch(self) -> None:
        self._sentinel()
        self._marker()
        cap = self.session.capture
        record = _fence_record(fence=self.session.fence, nonce=self.session.fence_nonce,
                               data=cap.raw(), sha256_prefix="ab" * 32)
        capture_mod.write_capture_fence(self.session._fence_path(), record)
        drained = self.session.drain_after_exit(budget_ms=200)
        self.assertEqual(drained["finality"], "mismatch", drained)
        self.assertEqual(drained["outcome"], capture_mod.OUTCOME_FENCE_MISMATCH, drained)
        self.assertFalse(runtime_mod._stream_is_final(drained))

    def test_a_legacy_finalized_record_is_refused_not_final(self) -> None:
        """# superseded by OS-48: `test_an_unproven_proof_names_the_holder_and_is_not_final`
        # -- the legacy record shape is refused BY NAME, whatever it says."""
        self._sentinel()
        cap = self.session.capture
        legacy = capture_mod.capture_finalized_path(cap.path, self.session.incarnation)
        Path(os.fsdecode(legacy)).write_text(json.dumps({
            "schema": capture_mod.LEGACY_FINALIZED_SCHEMA, "fence": self.session.fence,
            "finality": "proven", "total_bytes": cap.size, "sha256": cap.sha256}))
        drained = self.session.drain_after_exit(budget_ms=200)
        self.assertEqual(drained["finality"], capture_mod.OUTCOME_LEGACY_FINALIZED, drained)
        self.assertEqual(drained["outcome"], capture_mod.OUTCOME_LEGACY_FINALIZED, drained)
        self.assertFalse(runtime_mod._stream_is_final(drained))


class _FiredWitness:
    """A parent-death witness already in state `final` (the supervisor's death is stipulated
    for the forked-topology lock; the death rule itself is locked in test_os48_recovery_cuts)."""
    state = "final"

    def fired(self, _timeout: float = 0.0) -> str:
        return "final"

    def fds(self) -> set:
        return set()


def _run_watcher_finalize(base: Path, *, output: bytes, linger_child: bool,
                          budget_ms: int = 2000) -> dict:
    """Drive the EXACT production pty topology and run `_orphan_finalize` in the forked
    leader/watcher, returning the fence read back from disk plus the capture.

    leader (setsid + TIOCSCTTY, KEEPS one slave fd -- the owner-held reference) -> agent
    (setpgid + tcsetpgrp): the agent writes ``output``, optionally forks a child that LINGERS
    in the agent's process group holding the slave, then exits.  The leader reaps the agent,
    writes the fence marker through its slave reference and finalizes as an orphan.
    """
    import pty
    import termios
    import fcntl
    master, slave = pty.openpty()
    capture = os.fsencode(str(base / "capture.log"))
    nonce = "f" * 32
    fcntl.fcntl(master, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
    host_boot = pty_supervisor.host_boot_id()
    leader = os.fork()
    if leader == 0:                                        # pragma: no cover - forked child
        try:
            os.setsid()
            with contextlib.suppress(OSError):
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            os.dup2(slave, 0); os.dup2(slave, 1); os.dup2(slave, 2)
            agent = os.fork()
            if agent == 0:
                signal.signal(signal.SIGTTOU, signal.SIG_IGN)
                os.setpgid(0, 0)
                os.tcsetpgrp(0, os.getpgrp())
                if linger_child:
                    child = os.fork()
                    if child == 0:
                        # A descendant in the AGENT'S process group that keeps the slave
                        # (its inherited 0/1/2) open after the agent exits.
                        time.sleep(3.0)
                        os._exit(0)
                os.write(1, output)
                os._exit(0)
            null = os.open(os.devnull, os.O_RDWR)
            for fd in (0, 1, 2):
                os.dup2(null, fd)
            os.close(null)
            agent_start = pty_supervisor.proc_start_ticks(agent)
            appender = capture_mod.RawBoundedAppender(capture, limits=CaptureLimits())
            while True:
                done, status = os.waitpid(agent, os.WNOHANG)
                if done == agent:
                    break
                pty_supervisor._drain_once(master, appender, budget=0.02)
            written = pty_supervisor._write_marker_bounded(
                slave, capture_mod.marker_bytes(nonce), master, appender)
            pty_supervisor._orphan_finalize(
                master, slave, appender, capture=capture, fence="sess:i-w", fence_nonce=nonce,
                code=0, marker_written=written, sentinel=None, witness=_FiredWitness(),
                budget_s=budget_ms / 1000.0, host_boot_id=host_boot, agent_pid=agent,
                agent_start_id=agent_start)
            os._exit(0)
        except BaseException:
            os._exit(127)
    os.close(slave)
    _, status = os.waitpid(leader, 0)
    os.close(master)
    fence = capture_mod.read_capture_fence(capture_mod.capture_fence_path(capture, "i-w"),
                                           fence="sess:i-w")
    return {"fence": fence, "capture": (base / "capture.log").read_bytes(), "nonce": nonce,
            "leader_status": status,
            "release": capture_mod.read_release_record(
                capture_mod.release_record_path(capture, "i-w"), fence="sess:i-w")}


class Item1WatcherFinalizeOverRealPtyTests(unittest.TestCase):
    """The forked exit watcher's `_orphan_finalize` over a REAL pty pair in the production
    topology: a reaped agent -> the marker is the boundary and the fence binds the prefix;
    a descendant in the agent's group that keeps the slave open changes NOTHING about the
    boundary (its bytes, if any, are after N) -- no holder enumeration is consulted."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r9-i1w-"))
        self.addCleanup(shutil.rmtree, self.base, True)

    def _check(self, run: dict, output: bytes) -> None:
        fence = run["fence"]
        self.assertEqual(fence["outcome"], capture_mod.EVIDENCE_FINAL, fence)
        record = fence["record"]
        bound = capture_mod.fence_matches(record, capture=self.base / "capture.log",
                                          sentinel_code=None, sentinel_present=False)
        self.assertTrue(bound["matches"], bound)
        n = int(record["boundary"]["offset_n"])
        self.assertIn(output.replace(b"\n", b"\r\n"), run["capture"][:n])
        self.assertEqual(record["owner"]["owner_role"], capture_mod.OWNER_EXIT_WATCHER)
        self.assertEqual(int(record["owner"]["generation"]), 1)
        self.assertEqual(run["release"]["outcome"], capture_mod.EVIDENCE_FINAL, run["release"])
        self.assertGreater(int(run["release"]["record"]["offset_r"]), n)

    def test_a_reaped_agent_with_no_holder_publishes_the_fence(self) -> None:
        output = b'{"type":"result","subtype":"success"}\n'
        run = _run_watcher_finalize(self.base, output=output, linger_child=False)
        self._check(run, output)

    def test_a_retained_slave_descendant_changes_nothing_about_the_boundary(self) -> None:
        """# superseded by OS-48: `..._is_unproven_and_the_holder_is_named` -- a retained
        # slave holder no longer withholds finality; N is the marker, published at once."""
        output = b'{"type":"result"}\n'
        started = time.time()
        run = _run_watcher_finalize(self.base, output=output, linger_child=True, budget_ms=800)
        self._check(run, output)
        # The lingering holder (3 s) did not gate the publication: the leader finished well
        # before the holder's own exit.
        self.assertLess(time.time() - started, 2.5)


class Item1TotalFinalityGateTests(unittest.TestCase):
    """`await_completion`: an exit first observed AT (or past) the completion deadline
    still runs the finality gate.  Red at `fc21012`: the `while self._clock() < deadline`
    head skipped the drain when the exit landed exactly at the deadline, so the settlement
    escaped the gate.  OS-48: the gate's refusal is `boundary_unproven`."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r9-i1t-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        profile = profile_from_mapping(stub_profile_spec(
            "alive", worktree=str(self.base),
            timeouts={"completion_timeout_ms": 1, "post_exit_drain_budget_ms": 200}))
        self.session = runtime_mod.StandaloneSession(
            intent={"intent_id": "i-t", "run_id": "run_t", "role": "WORKER"},
            profile=profile, artifact_base=self.base, run_id="run_t",
            journal=journal_mod.ExecutionJournal(self.base, "run_t"))
        self.session.pty = None
        self.session.capture = capture_mod.BoundedCapture(self.base / "capture.log")
        self.session.capture.append(b'{"type":"result","subtype":"success"}\n', at="t")

    def test_an_exit_proven_at_the_deadline_still_gates_on_finality(self) -> None:
        s = pty_supervisor.exit_sentinel_path(self.base, "run_t", self.session.session_id,
                                              self.session.incarnation)
        s.parent.mkdir(parents=True, exist_ok=True)
        pty_supervisor.write_exit_sentinel(s, code=0, fence=self.session.fence)
        # completion_timeout_ms == 1: the deadline is already in the past on the first
        # iteration, yet the exit is proven -> the drain + finality gate must still run.
        result = self.session.await_completion()
        self.assertEqual(result["state"], "LOST", result)
        self.assertEqual(result["lost_reason"],
                         runtime_mod.lifecycle.resolve_unknown(
                             "boundary_unproven")["lost_reason"])
        self.assertIsNotNone(self.session.post_exit_drain,
                             "the finality gate never ran for a deadline-time exit")



# =====================================================================================
# Item 2 -- the crash-consistent prompt-composition upgrade
# =====================================================================================
def _stall_legacy_with_composition(base: Path, run_id: str):
    """A real omitted-thread run launched WITH an objective, stalled before its first
    dispatch, then downgraded to the exact pre-fix shape with NO persisted composition."""
    from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
    ledger = FileRuntimeStateStore(base / f"{run_id}.ledger.json")
    composition = launcher.prompt_composition_record(
        f"objective for {run_id}", requested_phases=("DESIGN",), risk="high",
        project_root=base, role_instructions={})
    adapter, state = launcher.build_standalone_adapter(
        {"run_id": run_id, "phases": ["DESIGN"], "max_iterations": 2},
        artifact_base=base, run_id=run_id, runtime_state=ledger,
        profile_spec=agent_profile_spec(worktree=str(base / "wt")),
        prompt_composition=composition)
    stalled = launcher.execute_state(
        state, adapter=adapter, runtime_state=ledger,
        journal=launcher._standalone_pause_row_journal(base, run_id),
        artifact_base=base, interrupt_before=["EXECUTE_INTENT"], audit_sink=None)
    assert stalled.get("pending_intent"), stalled.get("terminal_reason")
    target = launcher.standalone_authority_path(base, run_id, "")
    current = json.loads(target.read_text())
    legacy = {k: current[k] for k in ("schema", "run_id", "adapter", "runtime_state_path",
                                      "approval_authority", "profile_digest")}
    legacy["thread_id"] = ""                              # the pre-fix omitted-thread shape
    target.write_text(json.dumps(legacy, sort_keys=True, indent=2) + "\n")
    # The persisted composition is KEPT (this run launched WITH an objective): the
    # automatic upgrade binds it, which is the crash-consistency subject of item 2.
    return ledger, target, composition


class _UpgradeCrash(Exception):
    pass


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class Item2CrashConsistentUpgradeTests(unittest.TestCase):
    """The legacy-authority upgrade is a two-phase, reconcilable act.  Red at `fc21012`:
    `_write_upgraded_legacy_authority` rewrote the authority then appended its audit; a
    crash between left the new composition bound with NO audit record, and the replay was
    refused because the authority was no longer the legacy shape."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r9-i2-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt").mkdir()
        self._real_write = launcher._durable_write
        self._real_append = launcher._durable_append
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        launcher._durable_write = self._real_write        # type: ignore[assignment]
        launcher._durable_append = self._real_append      # type: ignore[assignment]

    def _authority(self, run_id: str) -> dict:
        return json.loads(launcher.standalone_authority_path(
            self.base, run_id, "").read_text())

    def _committed_upgrades(self, run_id: str) -> list:
        return list(launcher.read_authority_upgrades(self.base, run_id))

    def test_every_cut_point_converges_to_one_committed_record(self) -> None:
        for cut in ("before_prepared", "after_prepared", "after_rebind",
                    "before_committed", "after_committed"):
            with self.subTest(cut=cut):
                run_id = "run_up" + cut.replace("_", "")
                (self.base / "wt").mkdir(exist_ok=True)
                _ledger, _target, composition = _stall_legacy_with_composition(self.base, run_id)
                self._crash_at(run_id, composition, cut)
                # The read path reconciles: exactly one committed record, the authority is
                # the upgraded identity bound to the composition digest.
                launcher.load_standalone_authority(self.base, run_id)
                launcher.load_standalone_authority(self.base, run_id)   # idempotent
                committed = self._committed_upgrades(run_id)
                digest = launcher.prompt_composition_digest(composition)
                if cut == "before_prepared":
                    # nothing was ever attempted; the run is still the legacy shape and
                    # the NEXT read performs (and commits) the upgrade fresh.
                    self.assertEqual(len(committed), 1, committed)
                else:
                    self.assertEqual(len(committed), 1, committed)
                record = self._authority(run_id)
                self.assertEqual(record["thread_id"], launcher.DEFAULT_THREAD_ID)
                self.assertEqual(record["prompt_composition_digest"], digest)
                self.assertEqual(committed[0]["bound_prompt_composition_digest"], digest)

    def _crash_at(self, run_id: str, composition: dict, cut: str) -> None:
        target = launcher.standalone_authority_path(self.base, run_id, "")

        def append(path, text):
            self._real_append(path, text)
            if cut == "after_prepared" and '"state": "prepared"' in text:
                raise _UpgradeCrash(cut)
            if cut == "before_committed" and '"state": "prepared"' in text:
                # allow the rebind to happen, block just before committed by crashing on
                # the NEXT append (committed) -- handled below.
                pass
            if cut == "after_committed" and '"state": "committed"' in text:
                raise _UpgradeCrash(cut)

        def write(path, text):
            self._real_write(path, text)
            if cut in ("after_rebind", "before_committed") and Path(path) == target:
                raise _UpgradeCrash(cut)

        if cut == "before_prepared":
            return                                         # no crash: fresh upgrade next read
        launcher._durable_append = append                 # type: ignore[assignment]
        launcher._durable_write = write                   # type: ignore[assignment]
        try:
            with self.assertRaises(_UpgradeCrash):
                launcher.load_standalone_authority(self.base, run_id)
        finally:
            self._restore()

    def test_a_crash_after_rebind_never_leaves_the_binding_without_an_audit(self) -> None:
        run_id = "run_upaudit"
        _ledger, _target, composition = _stall_legacy_with_composition(self.base, run_id)
        self._crash_at(run_id, composition, "after_rebind")
        # After the crash the authority is the upgraded shape, but reconciliation on the
        # next read appends the committed audit record -- the binding is never audit-less.
        digest = launcher.prompt_composition_digest(composition)
        # Before reconciliation: the rebind landed, no committed audit yet.
        pre = self._committed_upgrades(run_id)
        self.assertEqual(pre, [], "a committed audit existed before reconciliation")
        launcher.load_standalone_authority(self.base, run_id)
        self.assertEqual(len(self._committed_upgrades(run_id)), 1)
        self.assertEqual(self._authority(run_id)["prompt_composition_digest"], digest)


# =====================================================================================
# Item 3 -- per-run watchdog isolation
# =====================================================================================
@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class Item3PerRunWatchdogIsolationTests(_CrashRoom):
    """A malformed authority for ONE run must not abort the whole fleet sweep.  Red at
    `fc21012`: `capabilities_for` propagates the authority `LauncherError` through the
    observation port and the whole `run_once` raises, so the healthy stalled run is never
    recovered."""

    def _profile(self) -> dict:
        return agent_profile_spec(
            worktree=str(self.base / "worktree"),
            driver_env={"OS37_GA_TURN_DELAY_MS": "3000",
                        "OS37_GA_TURN_DELAY_ROLE": "WORKER"},
            timeouts={"completion_timeout_ms": 30000})

    def _sweep_all(self) -> dict:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = launcher.run_watchdog_cli(
                ["watchdog", "once", "--artifact-base", str(self.base),
                 "--adapter", "standalone", "--json"])
        return {"code": code, "report": json.loads(out.getvalue().strip().splitlines()[-1]),
                "stderr": err.getvalue()}

    def test_a_corrupt_run_does_not_stop_the_healthy_run_from_being_recovered(self) -> None:
        from scripts.deterministic_workflow import coordinator_liveness
        # --- run H: a genuine crashed, stalled, recoverable run --------------------------
        healthy = "run_hisol"
        self.launch(healthy, self._profile())
        h_intent, h_spawned = self.await_delivery(healthy)
        h_pid = int(h_spawned["source_vocabulary"]["pid"])
        keeper = coordinator_liveness.begin_coordinator_liveness(
            healthy, artifact_base=self.base, lease_seconds=self.LEASE_SECONDS)
        self.kill_supervisor()
        if keeper is not None:
            keeper.stop()
        # --- run C: a launched run whose standalone authority is then CORRUPTED ----------
        corrupt = "run_cisol"
        c_ledger, _ = self.launch(corrupt, self._profile())
        c_intent, c_spawned = self.await_delivery(corrupt)
        c_pid = int(c_spawned["source_vocabulary"]["pid"])
        ckeeper = coordinator_liveness.begin_coordinator_liveness(
            corrupt, artifact_base=self.base, lease_seconds=self.LEASE_SECONDS)
        self.kill_supervisor()
        if ckeeper is not None:
            ckeeper.stop()
        auth = launcher.standalone_authority_path(self.base, corrupt, "t")
        auth.write_text('{"schema": "os37.standalone_authority.v1", "run_id": "ru')  # torn
        # Let both agents finish and every dead-owner lease lapse.
        deadline = time.time() + 30
        while time.time() < deadline and (pid_alive(h_pid) or pid_alive(c_pid)):
            time.sleep(0.1)
        time.sleep(self.LEASE_SECONDS + 0.5)
        for pid in (h_pid, c_pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        result = self._sweep_all()
        report = result["report"]
        rows = {row["run_id"]: row for row in report["runs"]}
        self.assertIn(healthy, rows, "the sweep aborted before observing the healthy run")
        self.assertIn(corrupt, rows, report)
        self.assertGreaterEqual(report["runs_observed"], 2)
        # The healthy run is classified and recovered.
        self.assertEqual(rows[healthy]["outcome_status"], "RECOVERED", rows[healthy])
        h_settled = [r for r in self.journal(healthy).rows_for(h_intent)
                     if r["kind"] == "SETTLEMENT_OBSERVED"]
        self.assertEqual(len(h_settled), 1, "the healthy run was not settled")
        # The corrupt run is reported BY NAME and never recovered.
        self.assertTrue(rows[corrupt].get("escalation"), rows[corrupt])
        self.assertNotEqual(rows[corrupt].get("outcome_status"), "RECOVERED")


# =====================================================================================
# Item 4 -- invalid worktree types are rejected
# =====================================================================================
class Item4InvalidWorktreeTypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r9-i4-"))
        self.addCleanup(shutil.rmtree, self.base, True)

    def test_a_non_string_worktree_is_refused_at_the_freeze_door(self) -> None:
        for bad in (123, [], {}, 1.5, True):
            with self.subTest(bad=bad):
                spec = stub_profile_spec("alive", worktree="/tmp/x")
                spec["worktree"] = bad
                with self.assertRaises(launcher.LauncherError) as caught:
                    launcher.freeze_profile_worktree(spec)
                self.assertIn(launcher.STANDALONE_PROFILE_WORKTREE_INVALID,
                              str(caught.exception))

    def test_a_non_string_worktree_never_reaches_an_archive(self) -> None:
        spec = stub_profile_spec("alive", worktree="/tmp/x")
        spec["worktree"] = []
        with self.assertRaises(launcher.LauncherError):
            launcher.persist_standalone_profile(self.base, "run_i4", spec)
        self.assertFalse((self.base / "runs").exists()
                         and any((self.base).rglob("profiles")))

    def test_the_profile_loader_refuses_a_non_string_worktree(self) -> None:
        spec = stub_profile_spec("alive", worktree="/tmp/x")
        spec["worktree"] = {}
        with self.assertRaises(ProfileError):
            profile_from_mapping(spec)

    def test_omitted_and_empty_still_mean_the_launch_cwd(self) -> None:
        for value, present in ((None, False), ("", True)):
            with self.subTest(value=value):
                spec = stub_profile_spec("alive", worktree="/tmp/x")
                if present:
                    spec["worktree"] = value
                else:
                    spec.pop("worktree")
                frozen = launcher.freeze_profile_worktree(spec, launch_base=self.base)
                self.assertEqual(frozen["worktree"], str(self.base))


# =====================================================================================
# Item 5 -- an existing profile archive is validated before authority bind
# =====================================================================================
class Item5ExistingArchiveValidatedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r9-i5-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt").mkdir()
        self.spec = launcher.freeze_profile_worktree(
            stub_profile_spec("alive", worktree=str(self.base / "wt")))
        self.digest = launcher.profile_digest(self.spec)

    def test_a_corrupt_existing_archive_refuses_the_normal_write_path(self) -> None:
        archive = launcher.profile_archive_path(self.base, "run_i5", self.digest)
        archive.parent.mkdir(parents=True, exist_ok=True)
        # An archive that sits at the digest path but does NOT hash to it (tampered).
        archive.write_text('{"driver": "claude", "binary": "tampered"}\n')
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.persist_standalone_profile(self.base, "run_i5", self.spec)
        self.assertIn(launcher.STANDALONE_PROFILE_ARCHIVE_INVALID, str(caught.exception))

    def test_a_valid_existing_archive_is_accepted(self) -> None:
        archive = launcher.profile_archive_path(self.base, "run_i5b", self.digest)
        archive.parent.mkdir(parents=True, exist_ok=True)
        launcher._durable_write(archive, launcher.profile_payload(self.spec) + "\n")
        # A byte-identical archive validates and publishing proceeds.
        launcher.persist_standalone_profile(self.base, "run_i5b", self.spec)
        self.assertTrue(launcher.standalone_profile_path(self.base, "run_i5b").exists())

    def test_a_schema_invalid_existing_archive_refuses_even_when_it_hashes(self) -> None:
        # The archive content-addresses correctly but is not a valid PROFILE (schema): the
        # production loader must reject it before authority binds.
        bad = dict(self.spec)
        bad["supported_range"] = "not-a-range"
        digest = launcher.profile_digest(bad)
        archive = launcher.profile_archive_path(self.base, "run_i5c", digest)
        archive.parent.mkdir(parents=True, exist_ok=True)
        launcher._durable_write(archive, launcher.profile_payload(bad) + "\n")
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.persist_standalone_profile(self.base, "run_i5c", bad)
        self.assertIn(launcher.STANDALONE_PROFILE_ARCHIVE_INVALID, str(caught.exception))


# =====================================================================================
# Item 6 -- a missing capture sha256 is never healed
# =====================================================================================
class Item6MissingDigestNeverHealedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r9-i6-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.path = self.base / "capture.log"
        self.path.write_bytes(b'{"type":"system","session_id":"s"}\n')
        self.meta = self.path.with_name(self.path.name + ".meta.json")

    def _write_meta(self, **over: Any) -> None:
        meta = {"schema": capture_mod.META_SCHEMA, "records": 1,
                "total_bytes": self.path.stat().st_size, "dropped_bytes": 0,
                "truncation": "", "sha256": capture_mod._digest_of(self.path).hexdigest(),
                "writer": capture_mod.WRITER_SUPERVISOR, "unanswerable": ""}
        meta.update(over)
        self.meta.write_text(json.dumps(meta))

    def _handoff(self) -> capture_mod.RawBoundedAppender:
        return capture_mod.RawBoundedAppender(os.fsencode(str(self.path)),
                                              limits=CaptureLimits())

    def test_an_empty_sha256_is_irreversibly_unanswerable_and_never_rewritten(self) -> None:
        self._write_meta(sha256="")
        appender = self._handoff()
        self.assertTrue(appender.unanswerable, "an empty digest was accepted at handoff")
        appender.append(b'{"type":"result"}\n')            # the watcher writes more
        appender.close()
        # The meta this writer produced never carries a freshly derived digest for the
        # inherited prefix: the empty string is preserved and the capture stays refused.
        meta = json.loads(self.meta.read_text())
        self.assertEqual(meta["sha256"], "", "the missing digest was HEALED at handoff")
        self.assertTrue(meta["unanswerable"])
        reader = capture_mod.BoundedCapture(self.path)
        self.assertFalse(reader.completion_is_answerable()["answerable"])

    def test_an_absent_sha256_key_is_refused(self) -> None:
        meta = {"schema": capture_mod.META_SCHEMA, "records": 1,
                "total_bytes": self.path.stat().st_size, "dropped_bytes": 0,
                "truncation": "", "writer": capture_mod.WRITER_SUPERVISOR,
                "unanswerable": ""}                        # NO sha256 key at all
        self.meta.write_text(json.dumps(meta))
        appender = self._handoff()
        self.assertTrue(appender.unanswerable)
        appender.close()
        self.assertFalse(capture_mod.BoundedCapture(self.path)
                         .completion_is_answerable()["answerable"])

    def test_a_malformed_digest_is_refused(self) -> None:
        self._write_meta(sha256="not-a-hex-digest")
        appender = self._handoff()
        self.assertTrue(appender.unanswerable)

    def test_the_reader_refuses_an_invalid_meta_shape_directly(self) -> None:
        self._write_meta(sha256="")
        integrity = capture_mod.BoundedCapture(self.path).integrity()
        self.assertFalse(integrity["consistent"])
        self.assertEqual(integrity["reason"], capture_mod.INTEGRITY_META_INVALID)

    def test_a_valid_closed_meta_is_still_answerable(self) -> None:
        self._write_meta()                                 # the closed v2 shape, valid digest
        self.assertTrue(capture_mod.BoundedCapture(self.path)
                        .completion_is_answerable()["answerable"])


# =====================================================================================
# Doc scope -- the omitted-thread compatibility scope is stated
# =====================================================================================
class DocScopeTests(unittest.TestCase):
    def test_the_conformance_doc_states_the_omitted_thread_only_scope(self) -> None:
        text = CONFORMANCE.read_text()
        self.assertIn("migrate-standalone-prompt-composition", text)
        self.assertIn("omitted-thread", text.lower().replace("omitted thread", "omitted-thread"))

    def test_an_explicit_thread_legacy_record_is_not_the_upgrade_shape(self) -> None:
        # The upgrade recognises ONLY the omitted-thread (`thread_id: ""`) legacy shape; a
        # legacy-looking record naming an explicit thread is not that shape and is left to
        # the normal validator, never silently upgraded.
        record = {"schema": launcher.STANDALONE_AUTHORITY_SCHEMA,
                  "adapter": launcher.STANDALONE_ADAPTER, "run_id": "run_x",
                  "runtime_state_path": "/x", "approval_authority": "none",
                  "profile_digest": "d" * 16, "thread_id": "explicit"}
        self.assertFalse(launcher._is_legacy_omitted_thread_authority(record, "run_x"))


if __name__ == "__main__":
    unittest.main()
