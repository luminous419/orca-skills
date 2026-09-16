"""OS-37 BUGFIX (run_829ca55e36c9): one behaviour-based lock per merge blocker of the
consolidated external review of head `fc21012` (issuecomment-5680361023).

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
# Item 1 -- the capture-finalized proof, distinct from the exit sentinel
# =====================================================================================
class Item1FinalizedProofContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r9-i1c-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.capture = self.base / "capture.log"
        self.capture.write_bytes(b'{"type":"result","subtype":"success"}\n')
        self.fence = "sess:i-1"

    def _proof(self, **over: Any) -> bytes:
        args = dict(fence=self.fence, finality=capture_mod.FINALITY_PROVEN,
                    writer=capture_mod.WRITER_EXIT_WATCHER, ended="hangup", errno_name="",
                    total_bytes=self.capture.stat().st_size,
                    sha256=capture_mod._digest_of(self.capture).hexdigest(),
                    records=1, exit_how="exit_sentinel", exit_code=0)
        args.update(over)
        path = capture_mod.capture_finalized_path(os.fsencode(str(self.capture)), "i-1")
        capture_mod.write_capture_finalized(path, **args)
        return path

    def test_a_proven_record_binds_the_capture_and_the_sentinel(self) -> None:
        path = self._proof()
        read = capture_mod.read_capture_finalized(path, fence=self.fence)
        self.assertEqual(read["outcome"], capture_mod.FINALITY_PROVEN)
        bound = capture_mod.finalized_matches(read["record"], capture=self.capture,
                                              sentinel_code=0, sentinel_present=True)
        self.assertTrue(bound["matches"], bound)

    def test_a_proof_over_a_capture_that_later_grew_no_longer_matches(self) -> None:
        path = self._proof()
        with open(self.capture, "ab") as handle:
            handle.write(b'{"forged":true}\n')            # a byte the proof never bound
        read = capture_mod.read_capture_finalized(path, fence=self.fence)
        bound = capture_mod.finalized_matches(read["record"], capture=self.capture,
                                              sentinel_code=0, sentinel_present=True)
        self.assertFalse(bound["matches"])
        self.assertEqual(bound["reason"], "capture_length_mismatch")

    def test_a_watcher_proof_that_cites_the_sentinel_needs_the_sentinel_to_agree(self) -> None:
        path = self._proof(exit_code=0)
        read = capture_mod.read_capture_finalized(path, fence=self.fence)
        # No sentinel, or a different code, refuses: the two files are one writer's act.
        self.assertFalse(capture_mod.finalized_matches(
            read["record"], capture=self.capture, sentinel_code=None,
            sentinel_present=False)["matches"])
        self.assertEqual(capture_mod.finalized_matches(
            read["record"], capture=self.capture, sentinel_code=7,
            sentinel_present=True)["reason"], "sentinel_code_mismatch")

    def test_a_foreign_fence_returns_no_record(self) -> None:
        path = self._proof()
        read = capture_mod.read_capture_finalized(path, fence="sess:i-other")
        self.assertEqual(read["outcome"], "foreign")
        self.assertIsNone(read["record"])


class Item1StreamFinalityIsTheProofTests(unittest.TestCase):
    """`_stream_is_final` accepts ONLY the capture-finalized proof; the exit sentinel
    alone -- the round-8 rule -- is refused (the hole item 1 closes)."""

    def test_only_the_finalized_proof_is_final(self) -> None:
        f = runtime_mod._stream_is_final
        self.assertTrue(f({"finality": "capture_finalized"}))
        for other in ("exit_sentinel", "exit_sentinel_only", "none", "unproven",
                      "mismatch", "foreign", ""):
            self.assertFalse(f({"finality": other}), other)
        # The round-8 shape a stale reader might build is no longer final.
        self.assertFalse(f({"ended": "no_master", "finality": "exit_sentinel"}))


class Item1MasterlessRequiresTheProofTests(unittest.TestCase):
    """An adopted (masterless) session settles finality ONLY from a matching finalized
    proof.  A sentinel with no proof -- the crashed-mid-drain supervisor -- is
    `exit_sentinel_only` and NOT final."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r9-i1m-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        profile = profile_from_mapping(stub_profile_spec("alive", worktree=str(self.base)))
        self.session = runtime_mod.StandaloneSession(
            intent={"intent_id": "i-m", "run_id": "run_m", "role": "WORKER"},
            profile=profile, artifact_base=self.base, run_id="run_m",
            journal=journal_mod.ExecutionJournal(self.base, "run_m"))
        self.session.pty = None                            # adopted: no master
        self.session.capture = capture_mod.BoundedCapture(self.base / "capture.log")
        self.session.capture.append(b'{"type":"result","subtype":"success"}\n', at="t")

    def _sentinel(self) -> Path:
        s = pty_supervisor.exit_sentinel_path(self.base, "run_m", self.session.session_id,
                                              self.session.incarnation)
        s.parent.mkdir(parents=True, exist_ok=True)
        pty_supervisor.write_exit_sentinel(s, code=0, fence=self.session.fence)
        return s

    def test_a_sentinel_with_no_finalized_proof_is_not_final(self) -> None:
        self._sentinel()
        drained = self.session.drain_after_exit(budget_ms=200)
        self.assertEqual(drained["finality"], "exit_sentinel_only", drained)
        self.assertFalse(runtime_mod._stream_is_final(drained))

    def test_the_matching_finalized_proof_is_final(self) -> None:
        self._sentinel()
        cap = self.session.capture
        capture_mod.write_capture_finalized(
            capture_mod.capture_finalized_path(cap.path, self.session.incarnation),
            fence=self.session.fence, finality=capture_mod.FINALITY_PROVEN,
            writer=capture_mod.WRITER_EXIT_WATCHER, ended="quiesced", errno_name="",
            total_bytes=cap.size, sha256=cap.sha256, records=1,
            exit_how="exit_sentinel", exit_code=0)
        drained = self.session.drain_after_exit(budget_ms=200)
        self.assertEqual(drained["finality"], "capture_finalized", drained)
        self.assertTrue(runtime_mod._stream_is_final(drained))

    def test_an_unproven_proof_names_the_holder_and_is_not_final(self) -> None:
        self._sentinel()
        cap = self.session.capture
        capture_mod.write_capture_finalized(
            capture_mod.capture_finalized_path(cap.path, self.session.incarnation),
            fence=self.session.fence, finality=capture_mod.FINALITY_UNPROVEN,
            writer=capture_mod.WRITER_EXIT_WATCHER, ended="budget", errno_name="",
            total_bytes=cap.size, sha256=cap.sha256, records=1,
            exit_how="exit_sentinel", exit_code=0,
            holders={"slave": "/dev/ttys9", "rows": [{"pid": 4242, "comm": "devserver"}]},
            detail="a descendant still holds the slave")
        drained = self.session.drain_after_exit(budget_ms=200)
        self.assertEqual(drained["finality"], "unproven", drained)
        self.assertFalse(runtime_mod._stream_is_final(drained))
        self.assertEqual(drained["holders"]["rows"][0]["comm"], "devserver")


def _run_watcher_finalize(base: Path, *, output: bytes, linger_child: bool,
                          budget_ms: int = 2000) -> dict:
    """Drive the EXACT production pty topology and run `_finalize_orphaned_capture` in the
    forked leader/watcher, returning the finalized record read back from disk.

    leader (setsid + TIOCSCTTY) -> agent (setpgid + tcsetpgrp): the agent writes ``output``,
    optionally forks a child that LINGERS in the agent's process group holding the slave,
    then exits.  The leader closes its slave, reaps the agent and finalizes -- reading the
    master while the leader (session leader) lives, exactly as production does.
    """
    import pty
    import termios
    import fcntl
    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)
    capture = os.fsencode(str(base / "capture.log"))
    finalized = capture_mod.capture_finalized_path(capture, "i-w")
    fcntl.fcntl(master, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
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
            os.close(slave)
            null = os.open(os.devnull, os.O_RDWR)
            for fd in (0, 1, 2):
                os.dup2(null, fd)
            os.close(null)
            os.waitpid(agent, 0)
            appender = capture_mod.RawBoundedAppender(capture, limits=CaptureLimits())
            pty_supervisor._finalize_orphaned_capture(
                master, appender, budget_s=budget_ms / 1000.0, finalized=finalized,
                fence="sess:i-w", code=0, slave_name=slave_name, settle_s=0.1)
            os._exit(0)
        except BaseException:
            os._exit(127)
    os.close(slave)
    os.waitpid(leader, 0)
    os.close(master)
    return capture_mod.read_capture_finalized(finalized, fence="sess:i-w")


class Item1WatcherFinalizeOverRealPtyTests(unittest.TestCase):
    """The forked exit watcher's `_finalize_orphaned_capture` over a REAL pty pair in the
    production topology: a reaped agent whose output quiesced with NO slave holder ->
    `proven`; a descendant in the agent's group that keeps the slave open -> `unproven`,
    holder NAMED."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r9-i1w-"))
        self.addCleanup(shutil.rmtree, self.base, True)

    def test_a_reaped_agent_that_quiesced_with_no_holder_is_proven(self) -> None:
        record = _run_watcher_finalize(
            self.base, output=b'{"type":"result","subtype":"success"}\n',
            linger_child=False)
        # The reaped agent's output quiesced and no descendant held the slave, so the
        # watcher's own drain proves the capture complete.  (Byte-for-byte completeness of
        # the drained output is covered end to end by
        # `test_os37_recovery_boundary_regressions.py::F01CrashedSupervisorDispatchIsCollectedTests`,
        # where the supervisor's main loop drains the agent's output during its run; here
        # the isolated agent writes and exits at once, and darwin discards a sole holder's
        # unread output on the last slave close, so this locks the FINALITY DECISION.)
        self.assertEqual(record["outcome"], capture_mod.FINALITY_PROVEN, record)
        # The proof binds the capture on disk (length + digest), whatever its size.
        bound = capture_mod.finalized_matches(
            record["record"], capture=self.base / "capture.log",
            sentinel_code=0, sentinel_present=True)
        self.assertTrue(bound["matches"], bound)

    def test_a_retained_slave_descendant_is_unproven_and_the_holder_is_named(self) -> None:
        record = _run_watcher_finalize(
            self.base, output=b'{"type":"result"}\n', linger_child=True, budget_ms=800)
        self.assertEqual(record["outcome"], capture_mod.FINALITY_UNPROVEN, record)
        holders = record["record"]["holders"]
        # Iteration 4: the orphan finalizer names the retained slave holder through the
        # COMPLETE libproc authority (`holders`/`unenumerable`); older foreground-group /
        # tty-row fields are accepted too for records written by pre-iteration-4 writers.
        named = (bool(holders.get("holders")) or bool(holders.get("unenumerable"))
                 or holders.get("foreground_group_present") is True
                 or bool(holders.get("rows")))
        self.assertTrue(named, f"the retained-slave holder was not named: {holders}")


class Item1TotalFinalityGateTests(unittest.TestCase):
    """`await_completion`: an exit first observed AT (or past) the completion deadline
    still runs the finality gate.  Red at `fc21012`: the `while self._clock() < deadline`
    head skipped the drain when the exit landed exactly at the deadline, so the settlement
    escaped the gate."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r9-i1t-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        profile = profile_from_mapping(stub_profile_spec(
            "alive", worktree=str(self.base), timeouts={"completion_timeout_ms": 1}))
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
                             "stream_end_unproven")["lost_reason"])
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
