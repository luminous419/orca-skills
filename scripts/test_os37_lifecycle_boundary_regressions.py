"""OS-37 BUGFIX (run_61c62f0bf91b): one lock per consolidated ROUND-4 review finding, 1-11,
against head `750d134` (issuecomment-5644516157).

Every test here FAILS (or ERRORS on an API the fix introduced) at `750d134` and passes after
the fix.  The findings sit at the lifecycle, recovery-authority and failure-routing
boundaries, so the assertions read DURABLE and OS-LEVEL state -- the journal, the ledger,
the exit sentinel, the capture file, the process table, this process's descriptor table --
and the review's own verification clause is honoured literally: supervisor-first death,
spawn-handoff failure, a detached-but-live pid, pid reuse, ownership-gated teardown and the
shebang wrapper are all driven with REAL subprocesses.

The invariant the whole module locks:

    No path may settle or release an intent until the same process incarnation is
    proven exited.  Recovery must reopen the exact authority and profile recorded by the
    original launch.  Runtime/infrastructure failure must never be converted into an
    agent verdict.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import inspect
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.deterministic_workflow import (executor, launcher,  # noqa: E402
                                            recovery_runtime,
                                            routing,
                                            standalone_env as env_policy,
                                            standalone_identity as identity,
                                            standalone_interrupt as interrupt_mod,
                                            standalone_journal as journal_mod,
                                            standalone_lifecycle as lifecycle,
                                            standalone_preflight as preflight_mod,
                                            standalone_pty as pty_supervisor,
                                            standalone_runtime as runtime_mod)
from scripts.deterministic_workflow.runtime_state import (  # noqa: E402
    FileRuntimeStateStore, InMemoryRuntimeStateStore)
from scripts.deterministic_workflow.standalone_profile import profile_from_mapping  # noqa: E402
from scripts.test_os37_external_review_regressions import execute_graph_cli  # noqa: E402
from scripts.test_os37_followup_review_regressions import (  # noqa: E402
    _Composed, _langgraph_ok, LANGGRAPH_REASON, agent_profile_spec, kill_and_reap,
    open_fds, pid_alive, stub_profile_spec, zombies_among)

REPO = Path(__file__).resolve().parent.parent


# =====================================================================================
# shared: a minimal real-binary profile over /bin/sh, and injected preflight legs
# =====================================================================================
def sh_profile(room: str, *script_args: str, timeouts: dict | None = None):
    """A profile whose agent IMAGE is `/bin/sh` (native) running a real script."""
    return profile_from_mapping({
        "driver": "claude", "binary": "sh", "supported_range": [[1, 0, 0], [9, 0, 0]],
        "bin_dirs": ["/bin"], "worktree": room,
        "readiness_records": [{"channel": "structured", "record_type": "system",
                               "session_field": "session_id"}],
        "delivery_proofs": [{"channel": "structured", "record_type": "assistant"}],
        "completion_records": [{"channel": "structured", "record_type": "result",
                                "error_field": "is_error"}],
        "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
        "identity_flag": "--session-id", "extra_args": list(script_args),
        "timeouts": {"graceful_force_timeout_ms": 500, "force_retry_ms": 20,
                     "physical_exit_timeout_ms": 1500, "staleness_budget_ms": 5000,
                     **(timeouts or {})}})


INJECTED_REHEARSALS = dict(
    rehearsal=lambda p, e, s: {"channel": "structured", "record_type": "system",
                               "session_id": s},
    mode_rehearsal=lambda p, e: {"r_b_closed": True, "delivery_proof": True,
                                 "auth_marker": None, "waited_without_prompt": True,
                                 "evaluable": True, "identity_bound": True, "detail": {}})
def alive_profile(room: str, **timeouts: int):
    """The native stub in `alive` mode: a REAL agent process that sleeps 30 s and emits
    nothing -- the interrupt ladder's own fixture -- whose image is the stub itself."""
    return profile_from_mapping(stub_profile_spec(
        "alive", worktree=room,
        timeouts={"graceful_force_timeout_ms": 500, "force_retry_ms": 20,
                  "physical_exit_timeout_ms": 1500, "staleness_budget_ms": 5000,
                  **timeouts}))


def _write_script(room: Path, name: str, body: str) -> str:
    path = room / name
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return str(path)


def _checkpoint_state(run) -> dict:
    """The run's OWN committed head, as a stranger process reads it."""
    from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
    saver = FileCheckpointSaver(run.checkpoint_path)
    stored = saver.get_tuple({"configurable": {"thread_id": "graph", "checkpoint_ns": ""}})
    return dict((stored.checkpoint or {}).get("channel_values") or {}) if stored else {}


# =====================================================================================
# F1 -- the agent and its exit evidence outlive the supervisor
# =====================================================================================
class F01SupervisorDeathTests(unittest.TestCase):
    """[P1] Supervisor-first death.  A REAL supervisor process spawns a real agent through
    `standalone_pty.spawn` and then dies with `os._exit(0)` while the agent runs."""

    SUPERVISOR = textwrap.dedent("""
        import json, os, sys, time
        sys.path.insert(0, sys.argv[1])
        from scripts.deterministic_workflow import standalone_pty as pty_supervisor
        from scripts.deterministic_workflow.standalone_profile import profile_from_mapping
        room, agent, handoff = sys.argv[2], sys.argv[3], sys.argv[4]
        profile = profile_from_mapping({
            "driver": "claude", "binary": "sh", "supported_range": [[1,0,0],[9,0,0]],
            "bin_dirs": ["/bin"], "worktree": room,
            "readiness_records": [{"channel": "structured", "record_type": "system",
                                   "session_field": "session_id"}],
            "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
            "identity_flag": "--session-id"})
        base = os.path.join(room, "artifacts")
        sentinel = pty_supervisor.exit_sentinel_path(base, "run_f01", "sess", "inc1")
        os.makedirs(os.path.dirname(sentinel), exist_ok=True)
        session = pty_supervisor.spawn(
            argv=["/bin/sh", agent], env={"PATH": "/bin:/usr/bin"}, profile=profile,
            session_id="sess", incarnation="inc1",
            spawn_record_target=pty_supervisor.spawn_record_path(base, "run_f01", "i", "inc1"),
            cwd=room, sentinel=str(sentinel), fence="sess:inc1", image="/bin/sh")
        time.sleep(0.4)
        pty_supervisor.drain(session["master_fd"], budget_ms=200)
        with open(handoff, "w") as fh:
            json.dump({"agent_pid": session["pid"], "leader_pid": session["leader_pid"],
                       "sentinel": str(sentinel),
                       "capture": os.path.join(os.path.dirname(sentinel), "capture.log"),
                       "supervisor_pid": os.getpid()}, fh)
        os._exit(0)          # THE SUPERVISOR DIES FIRST, the agent still running
    """)

    def setUp(self) -> None:
        self.room = Path(tempfile.mkdtemp(prefix="os37-r4f1-"))
        self.addCleanup(shutil.rmtree, self.room, True)
        self.agent = _write_script(self.room, "agent.sh", """
            for i in 1 2 3 4 5 6 7 8 9 10; do echo "tick $i"; sleep 0.2; done
            echo FINAL_LINE_AFTER_SUPERVISOR_DEATH
            exit 7
        """)
        self.handoff = self.room / "handoff.json"
        self.pids: list[int] = []
        self.addCleanup(lambda: kill_and_reap(*self.pids))

    def _run_supervisor(self) -> dict:
        proc = subprocess.run([sys.executable, "-c", self.SUPERVISOR, str(REPO),
                               str(self.room), self.agent, str(self.handoff)],
                              capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        handoff = json.loads(self.handoff.read_text())
        self.pids += [handoff["agent_pid"], handoff["leader_pid"]]
        return handoff

    def test_the_agent_survives_the_supervisor_and_its_exit_sentinel_is_written(self) -> None:
        handoff = self._run_supervisor()
        time.sleep(0.3)
        self.assertFalse(pid_alive(handoff["supervisor_pid"]), "the supervisor did not die")
        self.assertTrue(pid_alive(handoff["agent_pid"]),
                        "the agent died with the supervisor (the pty was hung up)")
        self.assertTrue(pid_alive(handoff["leader_pid"]),
                        "the exit watcher died with the supervisor; no sentinel can follow")
        deadline = time.time() + 15
        while time.time() < deadline and not os.path.exists(handoff["sentinel"]):
            time.sleep(0.1)
        self.assertTrue(os.path.exists(handoff["sentinel"]),
                        "no exit sentinel was written after the supervisor's death")
        proof = pty_supervisor.read_exit_sentinel(handoff["sentinel"], fence="sess:inc1")
        self.assertEqual(proof, {"outcome": "exited", "code": 7})
        time.sleep(0.3)
        self.assertFalse(pid_alive(handoff["agent_pid"]))
        self.assertFalse(pid_alive(handoff["leader_pid"]), "the watcher outlived its job")
        self.assertEqual(zombies_among(handoff["agent_pid"], handoff["leader_pid"]), [])
        capture = Path(handoff["capture"]).read_bytes()
        self.assertIn(b"FINAL_LINE_AFTER_SUPERVISOR_DEATH", capture,
                      "the output the agent wrote after the supervisor died did not reach "
                      "the capture; the orphaned watcher must drain the master")

    def test_the_supervisor_side_still_leaks_no_descriptor_and_no_zombie(self) -> None:
        """The keepalive copy lives in the WATCHER; the supervisor's own release closes
        the master and the guard end, and a completed dispatch leaves nothing behind."""
        before = open_fds()
        sentinel = self.room / "exit.inc9"
        session = pty_supervisor.spawn(
            argv=["/bin/sh", "-c", "exit 3"], env={"PATH": "/bin:/usr/bin"},
            profile=sh_profile(str(self.room)), session_id="s9", incarnation="inc9",
            spawn_record_target=str(self.room / "spawn.inc9"), cwd=str(self.room),
            sentinel=str(sentinel), fence="s9:inc9", image="/bin/sh")
        self.pids += [session["pid"], session["leader_pid"]]
        self.assertGreaterEqual(session["orphan_guard_fd"], 0)
        reaped = pty_supervisor.reap_leader(session, timeout_ms=5000)
        self.assertTrue(reaped["reaped"], reaped)
        self.assertEqual(reaped["status"], 3)
        pty_supervisor.release(session)
        self.assertEqual(session["master_fd"], -1)
        self.assertEqual(session["orphan_guard_fd"], -1)
        self.assertEqual(open_fds() - before, set(), "a descriptor leaked")
        self.assertEqual(pty_supervisor.read_exit_sentinel(sentinel, fence="s9:inc9"),
                         {"outcome": "exited", "code": 3})


# =====================================================================================
# F2 -- the watchdog reopens the authority the launch recorded
# =====================================================================================
class F02RecordedAuthorityTests(unittest.TestCase):
    """[P1] A run launched with `--runtime-state` must be recovered against THAT ledger."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r4f2-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.previous = os.environ.get(launcher.RUNTIME_STATE_DIR_ENV)
        os.environ[launcher.RUNTIME_STATE_DIR_ENV] = str(self.base / "default-ledgers")
        self.addCleanup(self._restore_env)
        (self.base / "worktree").mkdir()

    def _restore_env(self) -> None:
        if self.previous is None:
            os.environ.pop(launcher.RUNTIME_STATE_DIR_ENV, None)
        else:
            os.environ[launcher.RUNTIME_STATE_DIR_ENV] = self.previous

    def _compose(self, run_id: str, ledger_path: Path):
        ledger = FileRuntimeStateStore(ledger_path)
        adapter, state = launcher.build_standalone_adapter(
            {"run_id": run_id, "thread_id": "t", "phases": ["DESIGN"], "max_iterations": 2},
            artifact_base=self.base, run_id=run_id, runtime_state=ledger,
            profile_spec=agent_profile_spec(worktree=str(self.base / "worktree")))
        return adapter, state, ledger

    def test_the_launch_records_its_ledger_and_the_watchdog_reopens_exactly_it(self) -> None:
        custom = self.base / "elsewhere" / "custom-ledger.json"
        custom.parent.mkdir()
        self._compose("run_f2auth", custom)
        recorded = launcher.load_standalone_authority(self.base, "run_f2auth")
        self.assertIsNotNone(recorded, "the launch persisted no runtime-state authority")
        self.assertEqual(Path(recorded["runtime_state_path"]), custom.resolve())
        self.assertEqual(recorded["schema"], launcher.STANDALONE_AUTHORITY_SCHEMA)
        args = argparse.Namespace(artifact_base=str(self.base), results="",
                                  adapter="standalone", run_owner="", project_root="",
                                  standalone_profile="")
        wiring = launcher._watchdog_wiring(args)
        adapter, ledger, _journal = wiring.adapter_for("run_f2auth")
        self.assertEqual(ledger.path, custom.resolve(),
                         "the watchdog reconstructed the default ledger path instead of "
                         "reopening the authority the launch recorded")
        self.assertIs(adapter.runtime_state, ledger)
        self.assertFalse(launcher.default_runtime_state_path("run_f2auth", "t").exists(),
                         "a second, default ledger was created for this run")

    def test_a_run_that_recorded_no_authority_keeps_the_default(self) -> None:
        default = launcher.default_runtime_state_path("run_f2def", "t")
        self._compose("run_f2def", default)
        launcher.standalone_authority_path(self.base, "run_f2def").unlink()
        args = argparse.Namespace(artifact_base=str(self.base), results="",
                                  adapter="standalone", run_owner="", project_root="",
                                  standalone_profile="")
        _adapter, ledger, _journal = launcher._watchdog_wiring(args).adapter_for("run_f2def")
        # No head and no pause record exist for a run that never executed, so the thread
        # id the default path is keyed on falls back to the run id -- the pre-existing
        # rule; what matters here is that the DEFAULT directory is used and nothing else.
        self.assertEqual(ledger.path.parent, default.parent)
        self.assertEqual(ledger.path, launcher.default_runtime_state_path("run_f2def",
                                                                         "run_f2def"))

    def test_the_fake_and_orca_arms_are_byte_unchanged(self) -> None:
        source = inspect.getsource(launcher._watchdog_wiring)
        arm = source.split("if adapter_name == STANDALONE_ADAPTER:", 1)[0]
        self.assertIn("default_runtime_state_path(run_id, thread_id)", arm)
        self.assertNotIn("load_standalone_authority", arm)

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_a_stalled_run_launched_on_a_custom_ledger_recovers_into_that_ledger(self) -> None:
        custom = self.base / "custom" / "ledger.json"
        custom.parent.mkdir()
        adapter, state, ledger = self._compose("run_f2rt", custom)
        stalled = launcher.execute_state(
            state, adapter=adapter, runtime_state=ledger,
            journal=launcher._standalone_pause_row_journal(self.base, "run_f2rt"),
            artifact_base=self.base, interrupt_before=["EXECUTE_INTENT"], audit_sink=None)
        self.assertIsNone(stalled.get("terminal_status"))
        pending = stalled["pending_intent"]["intent_id"]
        # The custom ledger is the ONLY one that knows this run; the default must stay absent.
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = launcher.run_watchdog_cli(
                ["recover", "--run-id", "run_f2rt", "--artifact-base", str(self.base),
                 "--adapter", "standalone", "--json"])
        summary = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(code, 0, f"{summary!r}\n{err.getvalue()}")
        self.assertEqual(summary.get("status"), "RECOVERED", summary)
        self.assertIsNotNone(FileRuntimeStateStore(custom).get_settlement(pending),
                             "the recovery settled into a ledger other than the one the "
                             "launch was bound to")
        self.assertFalse(launcher.default_runtime_state_path("run_f2rt", "t").exists(),
                         "the recovery opened the DEFAULT ledger beside the recorded one")
        head = recovery_runtime.resolve_head("run_f2rt", artifact_base=self.base)
        self.assertEqual(head.state.get("terminal_status"), "COMPLETED",
                         head.state.get("terminal_reason"))


# =====================================================================================
# F3 -- tty absence is not proof of exit
# =====================================================================================
class F03DetachedButLivePidTests(unittest.TestCase):
    """[P1] A live pid absent from the captured tty: same incarnation -> NOT proven."""

    def setUp(self) -> None:
        self.stranger = subprocess.Popen(["sleep", "30"], start_new_session=True,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        self.addCleanup(self._stop)
        time.sleep(0.15)

    def _stop(self) -> None:
        self.stranger.kill()
        self.stranger.wait()

    def _record(self, **overrides) -> dict:
        record = {"pid": self.stranger.pid, "pgid": self.stranger.pid,
                  "sid": self.stranger.pid, "captured_tty": "ttys998"}
        record.update(overrides)
        return record

    def test_the_platform_reports_a_start_identity(self) -> None:
        """Without this axis every decision below is `unknown`; the MVP platform used to
        answer 0 for every process."""
        ticks = pty_supervisor.proc_start_ticks(self.stranger.pid)
        self.assertGreater(ticks, 0, "no start identity on this platform")
        self.assertEqual(pty_supervisor.proc_start_ticks(self.stranger.pid), ticks)
        self.assertEqual(pty_supervisor.proc_start_ticks(2 ** 22 - 7), 0)

    def test_a_detached_live_incarnation_is_not_proven_exited(self) -> None:
        ticks = pty_supervisor.proc_start_ticks(self.stranger.pid)
        snapshot = pty_supervisor.read_process_table("ttys998")
        self.assertTrue(snapshot["readable"])
        proof = pty_supervisor.exit_proven(self._record(proc_start_ticks=ticks), snapshot)
        self.assertFalse(proof["proven"], proof)
        self.assertEqual(proof["reason"], "incarnation_detached_but_live")
        self.assertIsNone(self.stranger.poll(), "the process is not even alive")

    def test_a_recycled_pid_with_another_start_identity_is_proven_gone(self) -> None:
        ticks = pty_supervisor.proc_start_ticks(self.stranger.pid)
        snapshot = pty_supervisor.read_process_table("ttys998")
        proof = pty_supervisor.exit_proven(self._record(proc_start_ticks=ticks - 1),
                                           snapshot)
        self.assertTrue(proof["proven"], proof)
        self.assertEqual(proof["reason"], "pid_recycled_start_identity_mismatch")

    def test_no_start_identity_on_either_side_stays_unknown(self) -> None:
        snapshot = pty_supervisor.read_process_table("ttys998")
        proof = pty_supervisor.exit_proven(self._record(), snapshot)
        self.assertFalse(proof["proven"], proof)
        self.assertTrue(proof["reason"].startswith("exit_unproven:"), proof)

    def test_the_interrupt_ladder_reports_exit_unproven_for_a_detached_live_agent(self) -> None:
        """The whole ladder over the same shape: nothing is signalled (the tty-scoped
        table has no row to verify against) and the outcome is `not_owned`/`exit_unproven`
        -- never `interrupted_confirmed`, which is what tty absence alone produced."""
        ticks = pty_supervisor.proc_start_ticks(self.stranger.pid)
        record = {"pid": self.stranger.pid, "pgid": self.stranger.pid,
                  "sid": self.stranger.pid, "captured_tty": "ttys998",
                  "run_id": "r", "repo_id": "x", "worktree_selector": "id:x::/w",
                  "agent_id": "a", "task_id": "t", "dispatch_id": "d", "session_id": "s",
                  "pty_id": "p", "process_incarnation": "i", "host_scope": "local",
                  "spawn_token": "tok", "started_at": "", "argv_digest": "",
                  "env_digest": "", "created_by_this_runtime": True,
                  "resource_kind": "pty_session", "user_taken_over": False,
                  "proc_start_ticks": ticks}
        profile = sh_profile("/", timeouts={"graceful_force_timeout_ms": 100})
        sent: list = []
        result = interrupt_mod.interrupt(
            "intent", "test", record=record, profile=profile,
            killpg=lambda *a: sent.append(("killpg", a)),
            kill=lambda *a: sent.append(("kill", a)))
        self.assertEqual(sent, [], "a signal reached a pid the table never verified")
        self.assertNotIn(result["interrupt_outcome"],
                         ("interrupted_confirmed", "terminated_forced"), result)
        self.assertIsNone(self.stranger.poll())

    def test_the_runtime_binds_the_start_identity_from_the_spawn_record(self) -> None:
        room = Path(tempfile.mkdtemp(prefix="os37-r4f3rt-"))
        self.addCleanup(shutil.rmtree, room, True)
        journal = journal_mod.ExecutionJournal(room, "run_f3")
        session = runtime_mod.StandaloneSession(
            intent={"intent_id": "intent-f3", "role": "WORKER"},
            profile=alive_profile(str(room)), artifact_base=room,
            run_id="run_f3", journal=journal)
        receipt = session.start(payload="x", **INJECTED_REHEARSALS)
        self.addCleanup(lambda: kill_and_reap(*[int(session.pty[k])
                                                for k in ("pid", "leader_pid")]))
        self.assertEqual(receipt["start_outcome"], "ready", receipt)
        self.assertTrue(session.record.get("proc_start_ticks"),
                        "the ownership record carries no start identity")
        self.assertEqual(session.record["proc_start_ticks"],
                         pty_supervisor.proc_start_ticks(session.record["pid"]))
        spawn_row = [r for r in journal.rows_for("intent-f3") if r["kind"] == "SPAWN_OBSERVED"]
        self.assertEqual(spawn_row[0]["source_vocabulary"]["spawn_record"]["proc_start_ticks"],
                         session.record["proc_start_ticks"])


# =====================================================================================
# F4 -- a failed handoff never abandons a live child
# =====================================================================================
class F04HandoffFailureTests(unittest.TestCase):
    """[P1] `_read_handoff` answers 0 after a REAL fork and a REAL execve."""

    def setUp(self) -> None:
        self.room = Path(tempfile.mkdtemp(prefix="os37-r4f4-"))
        self.addCleanup(shutil.rmtree, self.room, True)
        self.agent = _write_script(self.room, "agent.sh", "sleep 30\n")
        self.journal = journal_mod.ExecutionJournal(self.room, "run_f4")
        self.real_handoff = pty_supervisor._read_handoff
        self.addCleanup(setattr, pty_supervisor, "_read_handoff", self.real_handoff)
        self.pids: list[int] = []
        self.addCleanup(lambda: kill_and_reap(*self.pids))

    def _lose_handoff(self, delay: float) -> None:
        def lost(fd, **kw):
            time.sleep(delay)
            return 0
        pty_supervisor._read_handoff = lost

    def _session(self) -> runtime_mod.StandaloneSession:
        return runtime_mod.StandaloneSession(
            intent={"intent_id": "intent-f4", "role": "WORKER"},
            profile=alive_profile(str(self.room)), artifact_base=self.room,
            run_id="run_f4", journal=self.journal)

    def test_spawn_retains_the_pty_instead_of_hanging_it_up(self) -> None:
        self._lose_handoff(0.5)
        with self.assertRaises(pty_supervisor.SpawnHandoffFailed) as caught:
            pty_supervisor.spawn(
                argv=["/bin/sh", self.agent], env={"PATH": "/bin:/usr/bin"},
                profile=sh_profile(str(self.room)), session_id="s", incarnation="i4",
                spawn_record_target=str(self.room / "spawn.i4"), cwd=str(self.room),
                sentinel=str(self.room / "exit.i4"), fence="s:i4", image="/bin/sh")
        failure = caught.exception
        self.pids.append(failure.leader_pid)
        self.assertGreaterEqual(failure.master_fd, 0, "the master was closed; no authority")
        self.assertTrue(pid_alive(failure.leader_pid))
        record = json.loads((self.room / "spawn.i4").read_text())
        self.pids.append(record["pid"])
        self.assertTrue(pid_alive(record["pid"]), "the exec'd agent is gone already")
        retained = failure.retained_session()
        self.assertEqual(retained["pid"], 0)
        pty_supervisor.release(retained)

    def test_the_exec_d_child_is_found_terminated_and_proven_through_the_ladder(self) -> None:
        self._lose_handoff(0.7)
        session = self._session()
        receipt = session.start(payload="x", **INJECTED_REHEARSALS)
        self.pids += [int(session.record["pid"]), int(session.pty["leader_pid"])]
        self.assertEqual(receipt["start_outcome"], "failed", receipt)
        self.assertEqual(receipt["failure_reason"], "spawn_handoff_failed")
        self.assertEqual(receipt["teardown"], "proven",
                         "a start that forked and exec'd a child reported no teardown")
        self.assertFalse(pid_alive(int(session.record["pid"])), "the agent is still alive")
        rows = self.journal.rows_for("intent-f4")
        kinds = [(r["kind"], r["event"]) for r in rows]
        self.assertIn(("SPAWN_OBSERVED", "identity_bound"), kinds,
                      "the child's own spawn record was never bound")
        ladder = [r for r in rows if r["source_vocabulary"].get("ladder")]
        self.assertTrue(ladder, "no ownership ladder ran; the child was signalled bare "
                                "or not at all")
        reclaim = [r for r in rows if r["source_vocabulary"].get("master_fd_closed")]
        self.assertTrue(reclaim and reclaim[0]["source_vocabulary"]["leader_reaped"])
        self.assertEqual(rows[-1]["source_vocabulary"].get("teardown"), "proven")
        self.assertEqual(zombies_among(*self.pids), [])
        self.assertEqual(session.pty["master_fd"], -1)

    def test_a_child_that_cannot_be_proven_gone_is_retained_not_settled(self) -> None:
        """The ladder is REFUSED (the table reader cannot see the tty) -> no signal,
        `StandaloneTeardownUnproven`, and the resource stays retained with its authority."""
        self._lose_handoff(0.7)
        session = self._session()
        session._table_reader = lambda tty: {"tty": tty, "captured_at": time.time(),
                                             "rows": (), "readable": False}
        with self.assertRaises(identity.StandaloneTeardownUnproven):
            session.start(payload="x", **INJECTED_REHEARSALS)
        self.pids += [int(session.record["pid"]), int(session.pty["leader_pid"])]
        self.assertTrue(pid_alive(int(session.record["pid"])),
                        "a signal reached the child although ownership was unverifiable")
        self.assertGreaterEqual(session.pty["master_fd"], 0, "authority was dropped")
        rows = self.journal.rows_for("intent-f4")
        self.assertNotIn("SETTLEMENT_OBSERVED", [r["kind"] for r in rows])
        self.assertTrue(any(r["source_vocabulary"].get("retained") for r in rows))
        session.release()


# =====================================================================================
# F5 -- the failed-start teardown takes the ownership ladder
# =====================================================================================
class F05OwnershipGatedTeardownTests(unittest.TestCase):
    """[P1] PID reuse during the spawn-record wait: a REAL stranger holds the pid."""

    def setUp(self) -> None:
        self.room = Path(tempfile.mkdtemp(prefix="os37-r4f5-"))
        self.addCleanup(shutil.rmtree, self.room, True)
        self.stranger = subprocess.Popen(["sleep", "30"], start_new_session=True,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        self.addCleanup(self._stop)
        time.sleep(0.15)
        self.sent: list = []
        self.real = (os.kill, os.killpg)

        def spy_kill(pid, sig):
            if sig:
                self.sent.append(("kill", pid, sig))
            return self.real[0](pid, sig)

        def spy_killpg(pgid, sig):
            self.sent.append(("killpg", pgid, sig))
            return self.real[1](pgid, sig)
        os.kill, os.killpg = spy_kill, spy_killpg
        self.addCleanup(self._unspy)

    def _unspy(self) -> None:
        os.kill, os.killpg = self.real

    def _stop(self) -> None:
        self._unspy()
        self.stranger.kill()
        self.stranger.wait()

    def _session(self) -> runtime_mod.StandaloneSession:
        journal = journal_mod.ExecutionJournal(self.room, "run_f5")
        session = runtime_mod.StandaloneSession(
            intent={"intent_id": "intent-f5", "role": "WORKER"},
            profile=sh_profile(str(self.room)), artifact_base=self.room, run_id="run_f5",
            journal=journal, table_reader=pty_supervisor.read_process_table)
        pid = self.stranger.pid
        session.pty = {"master_fd": -1, "slave_name": "/dev/ttys996", "pid": pid,
                       "pgid": pid, "sid": pid - 1, "leader_pid": pid - 1,
                       "pty_id": "pty-f5", "argv": (), "orphan_guard_fd": -1}
        session.record = session._ownership_record(
            pid=pid, pgid=pid, sid=pid - 1, tty="ttys996", pty_id="pty-f5",
            argv_digest="x", env_digest="y")
        return session

    def test_a_recycled_pid_is_never_signalled_and_the_teardown_is_unproven(self) -> None:
        session = self._session()
        with self.assertRaises(identity.StandaloneTeardownUnproven):
            session._prove_teardown()
        self.assertEqual([s for s in self.sent if s[1] in (self.stranger.pid,)], [],
                         f"a bare signal reached the recycled pid: {self.sent}")
        self.assertIsNone(self.stranger.poll(), "the stranger was killed")
        rows = session.journal.rows_for("intent-f5")
        self.assertTrue(rows, "the refusal was not journalled")
        self.assertEqual(rows[-1]["kind"], "REFUSED")
        self.assertEqual(rows[-1]["source_vocabulary"].get("refusal"),
                         "refusal_is_not_a_transition")

    def test_no_bare_os_kill_remains_on_the_failed_start_path(self) -> None:
        source = inspect.getsource(runtime_mod.StandaloneSession._prove_teardown)
        self.assertNotIn("os.kill(", source)
        self.assertNotIn("os.waitpid(", source)
        self.assertIn("self.interrupt(", source)
        self.assertIn("exit_proven", source)

    def test_a_real_failed_start_still_proves_its_teardown_through_the_ladder(self) -> None:
        """The positive half: an agent that really exec'd and then never wrote a spawn
        record... cannot exist (the record precedes exec), so drive `_prove_teardown`
        directly over a REAL spawned child and assert the ladder -- gated, permitted --
        terminates it and the exit is proven by ESRCH, not by the signal having been sent."""
        self._unspy()
        room = self.room
        journal = journal_mod.ExecutionJournal(room, "run_f5real")
        session = runtime_mod.StandaloneSession(
            intent={"intent_id": "intent-f5r", "role": "WORKER"},
            profile=alive_profile(str(room)), artifact_base=room, run_id="run_f5real",
            journal=journal)
        receipt = session.start(payload="x", **INJECTED_REHEARSALS)
        self.assertEqual(receipt["start_outcome"], "ready", receipt)
        pid, leader = int(session.record["pid"]), int(session.pty["leader_pid"])
        self.addCleanup(lambda: kill_and_reap(pid, leader))
        self.assertEqual(session._prove_teardown(), "proven")
        self.assertFalse(pid_alive(pid))
        rows = journal.rows_for("intent-f5r")
        ladder = [r for r in rows if r["source_vocabulary"].get("ladder")]
        self.assertTrue(ladder)
        steps = [step["rung"] for step in ladder[-1]["source_vocabulary"]["ladder"]]
        self.assertIn("G1", steps)
        self.assertTrue(all(step["identity_verified"] for step in
                            ladder[-1]["source_vocabulary"]["ladder"]))
        self.assertEqual(zombies_among(pid, leader), [])


# =====================================================================================
# F6 -- a Reviewer's runtime failure is not a review verdict
# =====================================================================================
class F06ReviewerRuntimeFailureUnitTests(_Composed):

    def _dispatch_failure(self, run_id: str, role: str, exc: BaseException):
        adapter, _state, ledger = self.compose_spec(
            stub_profile_spec("ready", worktree=self.worktree), run_id=run_id)
        intent = self.intent(f"intent-{run_id}", role=role, run_id=run_id)
        session = adapter.runtime.session_for(intent)

        def raising(**_kwargs):
            raise exc
        session.run_dispatch = raising
        claim = ledger.claim(intent)
        try:
            return adapter.start(intent, lease_token=claim["lease_token"]), None, ledger, intent
        except executor.IdempotencyRecoveryError as caught:
            return None, caught, ledger, intent

    def test_a_reviewer_runtime_failure_is_a_typed_terminal_not_a_fail_verdict(self) -> None:
        for role in lifecycle.RUNTIME_FAILURE_NOT_A_VERDICT_ROLES:
            for number, exc in enumerate((
                    runtime_mod.StandaloneDispatchFailed("readiness_timed_out", "deadline"),
                    env_policy.SecretUnavailable("auth secret X is unresolvable"),
                    runtime_mod.StandaloneDispatchFailed("lost", "capture_truncated"))):
                run_id = f"run_f6{role[:1].lower()}{number}"
                with self.subTest(role=role, exc=type(exc).__name__):
                    receipt, caught, ledger, intent = self._dispatch_failure(run_id, role, exc)
                    self.assertIsNone(receipt, "the reviewer runtime failure SETTLED")
                    self.assertIsNotNone(caught)
                    self.assertEqual(caught.code, "REVIEWER_RUNTIME_FAILURE")
                    self.assertIsNone(ledger.get_settlement(intent["intent_id"]))
                    self.assertIsNone(adapter_settlement(self, run_id, intent["intent_id"]))
                    rows = self.journal(run_id).rows_for(intent["intent_id"])
                    self.assertNotIn("SETTLEMENT_OBSERVED", [r["kind"] for r in rows])
                    failure = [r for r in rows
                               if r["source_vocabulary"].get("runtime_failure")]
                    self.assertEqual(len(failure), 1)
                    self.assertEqual(failure[0]["axes"]["settlement"], "not_settled")
                    self.assertEqual(failure[0]["source_vocabulary"]["role"], role)
                    # nothing spawned: the claim's lease is released for a successor
                    record = ledger.get_receipt(intent["intent_id"])
                    self.assertEqual(record["status"], "CLAIMED")
                    self.assertLessEqual(record["lease_expires_at"], time.time() + 1)

    def test_a_worker_runtime_failure_keeps_the_workflow_s_own_blocked_status(self) -> None:
        receipt, caught, ledger, intent = self._dispatch_failure(
            "run_f6w", "WORKER", runtime_mod.StandaloneDispatchFailed("readiness_timed_out", "x"))
        self.assertIsNone(caught)
        self.assertEqual(receipt["outcome"], "failed")
        self.assertEqual(ledger.get_settlement(intent["intent_id"])["result"]["status"],
                         "BLOCKED")

    def test_a_failed_completion_verdict_of_a_reviewer_is_not_settled_either(self) -> None:
        """The exit-0-without-record and error-field shapes reach `_complete` as a FAILED
        completion; for a Reviewer that too is a runtime observation, not a verdict."""
        spec = agent_profile_spec(worktree=self.worktree,
                                  driver_env={"OS37_GA_AUTH_FAIL": "1"})
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f6auth")
        intent = self.intent("intent-f6auth", role="PHASE_REVIEWER", run_id="run_f6auth")
        claim = ledger.claim(intent)
        with self.assertRaises(executor.IdempotencyRecoveryError) as caught:
            adapter.start(intent, lease_token=claim["lease_token"])
        self.assertEqual(caught.exception.code, "REVIEWER_RUNTIME_FAILURE")
        self.assertIsNone(ledger.get_settlement("intent-f6auth"))
        rows = self.journal("run_f6auth").rows_for("intent-f6auth")
        failure = [r for r in rows if r["source_vocabulary"].get("runtime_failure")]
        self.assertEqual(failure[0]["source_vocabulary"]["runtime_failure"]["reason"],
                         "error_field_set")
        self.assertEqual(failure[0]["source_vocabulary"]["runtime_failure"]["exit_status"], 1)
        self.assertEqual(failure[0]["axes"]["process_liveness"], "already exited")
        pid = int(failure[0]["source_vocabulary"]["pid"])
        self.assertFalse(pid_alive(pid))
        self.assertEqual(ledger.get_receipt("intent-f6auth")["status"], "EFFECTED")


def adapter_settlement(case, run_id: str, intent_id: str):
    for adapter in case.adapters:
        if adapter.run_id == run_id:
            return adapter.settlement(intent_id)
    return None


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class F06ReviewerRuntimeFailureGraphTests(unittest.TestCase):
    """Through the REAL `run_workflow.py --adapter standalone`: the phase Reviewer is
    OOM-killed (exit 9, no record).  No correction Worker, no phase iteration."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.room = Path(tempfile.mkdtemp(prefix="os37-r4f6-"))
        cls.outcome = execute_graph_cli(
            cls.room, run_id="run_r4f6", max_iterations=3,
            driver_env={"OS37_GA_EXIT_CODE_ONCE": str(cls.room / "once"),
                        "OS37_GA_EXIT_CODE": "9",
                        "OS37_GA_EXIT_CODE_ROLE": "PHASE_REVIEWER"},
            timeouts={"completion_timeout_ms": 4_000})

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.room, ignore_errors=True)

    def test_the_run_stops_typed_and_dispatches_no_correction(self) -> None:
        self.assertIsNone(self.outcome.escaped, repr(self.outcome.escaped))
        self.assertEqual(self.outcome.summary.get("terminal_status"), "BLOCKED", self.outcome.summary)
        self.assertEqual((self.outcome.summary.get("terminal_reason") or {}).get("code"),
                         "REVIEWER_RUNTIME_FAILURE")
        roles = [r["source_vocabulary"].get("terminal_role") for r in self.outcome.spawn_rows()]
        self.assertEqual(sorted(roles), ["PHASE_REVIEWER", "WORKER"],
                         f"a correction or repair round was dispatched: {roles}")
        intents = [r for r in self.outcome.journal_rows() if r["kind"] == "DELIVERY_INTENT"]
        self.assertEqual(len(intents), 2, "more than two dispatches were journalled")

    def test_no_reviewer_settlement_exists_and_no_iteration_was_charged(self) -> None:
        settled_roles = {(r["source_vocabulary"].get("terminal_role")) for r in
                         self.outcome.settlement_rows()}
        self.assertEqual(settled_roles, {"WORKER"},
                         "a Reviewer runtime failure produced a settlement")
        failure = [r for r in self.outcome.journal_rows()
                   if r["source_vocabulary"].get("runtime_failure")]
        self.assertEqual(len(failure), 1)
        self.assertEqual(failure[0]["source_vocabulary"]["runtime_failure"]["exit_status"], 9)
        self.assertEqual(failure[0]["source_vocabulary"]["runtime_failure"]["stage"], "lost")
        head = _checkpoint_state(self.outcome)
        self.assertIsNone(head.get("reviewer_result"), head.get("reviewer_result"))
        self.assertEqual(head.get("phase_iterations"), {"DESIGN": 0})
        self.assertEqual(head.get("repair_attempts", 0), 0)
        ledger = FileRuntimeStateStore(self.outcome.ledger_path)
        reviewer_intent = failure[0]["intent_id"]
        self.assertEqual(ledger.get_receipt(reviewer_intent)["status"], "EFFECTED")
        self.assertIsNone(ledger.get_settlement(reviewer_intent))
        pid = int(failure[0]["source_vocabulary"]["pid"])
        self.assertFalse(pid_alive(pid))

    def test_the_shared_router_would_have_corrected_a_fail_verdict(self) -> None:
        """The property is standalone-side: `routing` still routes a genuine FAIL to
        correction, byte-unchanged, and the runtime simply never hands it one."""
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        from scripts.deterministic_workflow.state import initial_state
        state = dict(initial_state(run_id="run_r4route", thread_id="t", phases=("DESIGN",),
                                   capabilities=BASE_CAPABILITIES, risk="high",
                                   max_iterations=3))
        state.update(worker_result={"status": "COMPLETE", "unit_test_status": "PASS"},
                     reviewer_result={"result": "FAIL", "findings": []})
        self.assertEqual(routing.route(state), "PREPARE_CORRECTION")


# =====================================================================================
# F7 -- every environment failure is in the closed table
# =====================================================================================
class F07EnvironmentFailuresAreTypedTests(_Composed):

    def test_secret_and_leak_failures_are_members_of_the_closed_table(self) -> None:
        self.assertEqual(runtime_mod.failure_stage_for(env_policy.SecretUnavailable("x")),
                         "secret_unavailable")
        self.assertEqual(runtime_mod.failure_stage_for(env_policy.ChildEnvironmentLeak("x")),
                         "child_environment_leak")
        self.assertEqual(runtime_mod.failure_stage_for(preflight_mod.PreflightRefused("x", ())),
                         "preflight_refused")
        self.assertEqual(runtime_mod.failure_stage_for(journal_mod.JournalUnreadable("x")),
                         "journal_unreadable")
        self.assertIsNone(runtime_mod.failure_stage_for(TypeError("x")))
        names = {kind.__name__ for kind, _stage in runtime_mod.FAILURE_STAGE_TABLE}
        self.assertTrue({"SecretUnavailable", "ChildEnvironmentLeak"} <= names)

    def test_a_missing_secret_reaches_a_typed_terminal_with_the_ledger_settled(self) -> None:
        """A Worker whose declared credential does not resolve, through `adapter.start`:
        no traceback, a typed FAILED settlement naming the stage, ledger SETTLED."""
        spec = stub_profile_spec("ready", worktree=self.worktree)
        spec["auth_secret_ref"] = {"ANTHROPIC_API_KEY": "OS37_R4_F7_ABSENT_SECRET"}
        os.environ.pop("OS37_R4_F7_ABSENT_SECRET", None)
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f7")
        intent = self.intent("intent-f7", run_id="run_f7")
        claim = ledger.claim(intent)
        receipt = adapter.start(intent, lease_token=claim["lease_token"])
        self.assertEqual(receipt["outcome"], "failed")
        self.assertEqual(receipt["failure_stage"], "secret_unavailable")
        self.assertEqual(receipt["teardown"], "not_required")
        self.assertEqual(ledger.get_receipt("intent-f7")["status"], "SETTLED")
        event = ledger.get_settlement("intent-f7")
        self.assertEqual(event["result"]["status"], "BLOCKED")
        self.assertEqual(event["result"]["standalone_failure"]["stage"], "secret_unavailable")
        rows = self.settlement_rows("run_f7", "intent-f7")
        self.assertEqual(rows[0]["source_vocabulary"]["completion_verdict"]["stage"],
                         "secret_unavailable")
        self.assertEqual(adapter.runtime.session("intent-f7").record, None)

    def test_a_child_environment_leak_reaches_a_typed_terminal(self) -> None:
        spec = stub_profile_spec("ready", worktree=self.worktree)
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f7leak")
        intent = self.intent("intent-f7leak", run_id="run_f7leak")
        session = adapter.runtime.session_for(intent)
        real = env_policy.build_child_env

        def leaking(*args, **kwargs):
            raise env_policy.ChildEnvironmentLeak("CLAUDE_CODE_MESSAGING_SOCKET leaked")
        env_policy.build_child_env = leaking
        self.addCleanup(setattr, env_policy, "build_child_env", real)
        claim = ledger.claim(intent)
        receipt = adapter.start(intent, lease_token=claim["lease_token"])
        self.assertEqual(receipt["failure_stage"], "child_environment_leak")
        self.assertEqual(ledger.get_receipt("intent-f7leak")["status"], "SETTLED")
        self.assertIsNone(session.record)

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_through_the_graph_a_missing_secret_is_a_named_block_not_a_traceback(self) -> None:
        room = Path(tempfile.mkdtemp(prefix="os37-r4f7g-"))
        self.addCleanup(shutil.rmtree, room, True)
        from scripts.test_os37_external_review_regressions import GRAPH_CREDENTIAL_ENV
        real_run_cli = launcher.run_cli

        def without_credential(argv):
            os.environ.pop(GRAPH_CREDENTIAL_ENV, None)
            return real_run_cli(argv)
        launcher.run_cli = without_credential
        self.addCleanup(setattr, launcher, "run_cli", real_run_cli)
        run = execute_graph_cli(room, run_id="run_r4f7g", credential=True)
        self.assertIsNone(run.escaped, repr(run.escaped))
        self.assertEqual(run.summary.get("terminal_status"), "BLOCKED", run.summary)
        rows = run.settlement_rows()
        self.assertTrue(rows)
        self.assertEqual(rows[0]["source_vocabulary"]["completion_verdict"]["stage"],
                         "secret_unavailable")
        ledger = FileRuntimeStateStore(run.ledger_path)
        self.assertEqual(ledger.get_receipt(rows[0]["intent_id"])["status"], "SETTLED")


# =====================================================================================
# F8 -- the profile rename is crash-durable
# =====================================================================================
class F08DirectoryFsyncTests(unittest.TestCase):

    def test_each_rename_is_followed_by_a_directory_fsync(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="os37-r4f8-"))
        self.addCleanup(shutil.rmtree, base, True)
        synced: list[str] = []
        real_fsync, real_open = os.fsync, os.open
        opened: dict[int, str] = {}

        def spy_open(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            opened[fd] = os.fspath(path)
            return fd

        def spy_fsync(fd):
            path = opened.get(fd)
            if path and os.path.isdir(path):
                synced.append(os.path.realpath(path))
            return real_fsync(fd)
        os.open, os.fsync = spy_open, spy_fsync
        try:
            target = launcher.persist_standalone_profile(base, "run_f8", {"driver": "claude"})
            launcher.persist_standalone_profile(base, "run_f8", {"driver": "codex"})
            authority = launcher.persist_standalone_authority(
                base, "run_f8", runtime_state_path=base / "ledger.json")
        finally:
            os.open, os.fsync = real_open, real_fsync
        archive_dir = os.path.realpath(target.parent / "profiles")
        current_dir = os.path.realpath(target.parent)
        # first call: archive + current; second: a new archive + current rewritten; the
        # authority record: its directory once more.
        self.assertGreaterEqual(synced.count(archive_dir), 2, synced)
        self.assertGreaterEqual(synced.count(current_dir), 3, synced)
        self.assertEqual(authority.parent, target.parent)

    def test_the_writer_names_the_discipline(self) -> None:
        source = inspect.getsource(launcher._durable_write)
        self.assertIn("os.replace(tmp, path)", source)
        self.assertIn("_fsync_directory(path.parent)", source)
        self.assertNotIn("os.replace", inspect.getsource(launcher.persist_standalone_profile))


# =====================================================================================
# F9 -- the result body is bound to the dispatch that produced it
# =====================================================================================
class F09ResultBodyProvenanceTests(_Composed):

    STALE = "STATUS: COMPLETE\nDECISION_GATE_STATE: CLEAR\n(a PREVIOUS dispatch's body)\n"

    def _spec(self, room: Path) -> dict:
        spec = stub_profile_spec("ready", worktree=self.worktree)
        spec["result_body_records"] = [{"channel": "structured", "record_type": "result",
                                        "body_field": "result"}]
        spec["output_last_message_path"] = str(room / "shared-last-message.md")
        return spec

    def test_the_path_is_scoped_per_dispatch_and_the_shared_file_is_never_read(self) -> None:
        room = self.base
        (room / "shared-last-message.md").write_text(self.STALE)
        adapter, _state, ledger = self.compose_spec(self._spec(room), run_id="run_f9")
        first = adapter.runtime.session_for(self.intent("intent-f9-a", run_id="run_f9"))
        second = adapter.runtime.session_for(self.intent("intent-f9-b", run_id="run_f9"))
        self.assertTrue(first.last_message_path and second.last_message_path)
        self.assertNotEqual(first.last_message_path, second.last_message_path)
        for session in (first, second):
            self.assertIn(session.session_id, session.last_message_path)
            self.assertIn(session.incarnation, session.last_message_path)
            self.assertEqual(session.driver.profile.output_last_message_path,
                             session.last_message_path)
            argv = session.driver.argv(session_id=session.session_id)
            self.assertNotIn(str(room / "shared-last-message.md"), argv)
            # the declared literal is never consulted, even though it holds a body
            body = session.driver.result_body("")
            self.assertIsNone(body["body"], body)
        # The driver that composes `-o` (codex) composes the SCOPED path, never the literal.
        codex = profile_from_mapping({
            "driver": "codex", "binary": "os37-stub-cli",
            "supported_range": [[0, 1, 0], [9, 0, 0]], "bin_dirs": first.profile.bin_dirs,
            "worktree": self.worktree, "delivery_mode": "launch_with_prompt",
            "identity_binding": "adopted",
            "readiness_records": [{"channel": "structured", "record_type": "thread.started",
                                   "session_field": "thread_id"}],
            "delivery_proofs": [{"channel": "structured", "record_type": "item.started"}],
            "completion_records": [{"channel": "structured", "record_type": "turn.completed"}],
            "result_body_records": [{"channel": "structured", "record_type": "item.completed",
                                     "body_field": "item.text"}],
            "output_last_message_path": str(room / "shared-last-message.md")})
        codex_session = runtime_mod.StandaloneSession(
            intent={"intent_id": "intent-f9-codex", "role": "WORKER"}, profile=codex,
            artifact_base=room, run_id="run_f9", journal=self.journal("run_f9"))
        argv = codex_session.driver.launch_argv(session_id="x", prompt="p")
        self.assertIn("-o", argv)
        self.assertEqual(argv[argv.index("-o") + 1], codex_session.last_message_path)
        self.assertNotIn(str(room / "shared-last-message.md"), argv)
        rehearsal = first._rehearsal_profile(first.profile, "mode").output_last_message_path
        self.assertNotEqual(rehearsal, first.last_message_path)
        self.assertNotEqual(rehearsal, str(room / "shared-last-message.md"))

    def test_sequential_dispatches_never_read_each_other_s_file(self) -> None:
        room = self.base
        adapter, _state, ledger = self.compose_spec(self._spec(room), run_id="run_f9seq")
        first = adapter.runtime.session_for(self.intent("intent-f9s-a", run_id="run_f9seq"))
        Path(first.last_message_path).parent.mkdir(parents=True, exist_ok=True)
        Path(first.last_message_path).write_text("STATUS: COMPLETE\nfirst dispatch body\n")
        extracted = first.driver.result_body("")
        self.assertEqual(extracted["source"], "output_last_message_path")
        self.assertEqual(extracted["path"], first.last_message_path)
        self.assertEqual(extracted["sha256"], hashlib.sha256(
            Path(first.last_message_path).read_bytes()).hexdigest())
        second = adapter.runtime.session_for(self.intent("intent-f9s-b", run_id="run_f9seq"))
        self.assertEqual(second.driver.result_body("")["body"], None,
                         "the second dispatch read the first dispatch's file")
        provenance = first._body_provenance(extracted)
        self.assertTrue(provenance["scoped_to_this_dispatch"])
        self.assertEqual(provenance["session_id"], first.session_id)
        self.assertEqual(provenance["process_incarnation"], first.incarnation)

    def test_concurrent_dispatches_hold_disjoint_paths(self) -> None:
        room = self.base
        adapter, _state, _ledger = self.compose_spec(self._spec(room), run_id="run_f9con")
        sessions = [adapter.runtime.session_for(self.intent(f"intent-f9c-{n}", run_id="run_f9con"))
                    for n in range(4)]
        paths = {s.last_message_path for s in sessions}
        self.assertEqual(len(paths), 4)
        for session in sessions:
            Path(session.last_message_path).parent.mkdir(parents=True, exist_ok=True)
            Path(session.last_message_path).write_text(f"body of {session.incarnation}\n")
        for session in sessions:
            self.assertEqual(session.driver.result_body("")["body"],
                             f"body of {session.incarnation}\n")

    def test_a_settled_dispatch_journals_where_its_body_came_from(self) -> None:
        spec = agent_profile_spec(worktree=self.worktree)
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f9j")
        intent = self.intent("intent-f9j", run_id="run_f9j")
        receipt, _event = self.dispatch(adapter, ledger, intent)
        self.assertEqual(receipt["outcome"], "succeeded", receipt)
        row = self.settlement_rows("run_f9j", "intent-f9j")[0]
        provenance = row["source_vocabulary"]["result_body_provenance"]
        self.assertEqual(provenance["source"], "result.result")
        self.assertEqual(provenance["session_id"], row["session_id"])
        self.assertEqual(provenance["process_incarnation"], row["process_incarnation"])


# =====================================================================================
# F10 -- a shebang wrapper is refused by name in preflight
# =====================================================================================
class F10ShebangWrapperTests(_Composed):

    def test_an_actual_shebang_wrapper_is_refused_before_any_spawn(self) -> None:
        bindir = self.base / "bin"
        bindir.mkdir()
        wrapper = bindir / "os37-wrapped-cli"
        wrapper.write_text("#!/usr/bin/env python3\nimport sys\nprint('os37-wrapped-cli 1.2.3')\n")
        wrapper.chmod(0o755)
        spec = stub_profile_spec("ready", worktree=self.worktree)
        spec["binary"] = "os37-wrapped-cli"
        spec["bin_dirs"] = [str(bindir)]
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f10")
        intent = self.intent("intent-f10", run_id="run_f10")
        session = adapter.runtime.session_for(intent)
        spawned: list = []
        session._spawner = lambda **kw: spawned.append(kw) or (_ for _ in ()).throw(
            AssertionError("a wrapper reached the spawn"))
        claim = ledger.claim(intent)
        receipt = adapter.start(intent, lease_token=claim["lease_token"])
        self.assertEqual(receipt["outcome"], "failed")
        self.assertEqual(spawned, [], "preflight let a #! wrapper through to the spawn")
        rows = self.journal("run_f10").rows_for("intent-f10")
        refused = [r for r in rows if r["kind"] == "REFUSED"
                   and r["source_vocabulary"].get("preflight")]
        self.assertTrue(refused)
        self.assertEqual(refused[0]["source_vocabulary"]["reason"],
                         "binary_wrapper_unsupported")
        binary = [o for o in refused[0]["source_vocabulary"]["preflight"]
                  if o["check"] == "binary"][0]
        self.assertEqual(binary["reason"], "binary_wrapper_unsupported")
        self.assertEqual(binary["evidence"]["interpreter"], "/usr/bin/env")
        # CI-1 (correction iteration 2).  The product resolves `env python3` against the
        # CHILD's PATH and reports the REAL image (`realpath`); on ubuntu that is
        # `/usr/bin/python3.1X`, on darwin `/usr/bin/python3` -- so the expectation is
        # derived the same way on both platforms and compared EXACTLY, never by suffix.
        child_path = env_policy.build_child_env(session.profile, spawn_token="t",
                                                include_secrets=False)["PATH"]
        resolved = shutil.which("python3", path=child_path)
        expected_image = os.path.realpath(resolved) if resolved else ""
        self.assertEqual(binary["evidence"]["interpreter_image"], expected_image,
                         binary["evidence"])
        if not resolved:
            # A child PATH with no `python3` at all (a slim container): the image is
            # honestly EMPTY and the unresolved name is reported, never a fabricated path.
            self.assertEqual(binary["evidence"]["unresolved_interpreter"], "python3")
        self.assertEqual(binary["evidence"]["shebang"], "/usr/bin/env python3")
        event = ledger.get_settlement("intent-f10")
        self.assertEqual(event["result"]["standalone_failure"]["reason"],
                         "binary_wrapper_unsupported")

    def test_a_native_image_is_not_a_wrapper(self) -> None:
        self.assertIsNone(preflight_mod.interpreter_wrapper("/bin/sh", {}))
        self.assertIn("binary_wrapper_unsupported", preflight_mod.REASONS)
        stub = stub_profile_spec("ready", worktree=self.worktree)
        outcome = preflight_mod.check_binary(
            profile_from_mapping(stub), {"PATH": stub["bin_dirs"][0]})
        self.assertEqual(outcome["verdict"], "pass", outcome)


# =====================================================================================
# F11 -- safe run-scoped preflight checks are cached; auth is refreshed
# =====================================================================================
class F11PreflightCacheTests(_Composed):

    def _adapter(self, run_id: str):
        spec = agent_profile_spec(worktree=self.worktree)
        return self.compose_spec(spec, run_id=run_id)

    def test_the_rehearsals_run_once_per_run_and_auth_every_dispatch(self) -> None:
        adapter, _state, ledger = self._adapter("run_f11")
        rehearsals: list[str] = []
        auth_probes: list[list[str]] = []
        real_probe = preflight_mod.probe_on_pty

        def counting_probe(argv, env, **kwargs):
            if "auth" in argv:
                auth_probes.append(list(argv))
            return real_probe(argv, env, **kwargs)
        preflight_mod.probe_on_pty = counting_probe
        self.addCleanup(setattr, preflight_mod, "probe_on_pty", real_probe)
        for number in range(3):
            intent = self.intent(f"intent-f11-{number}", run_id="run_f11")
            session = adapter.runtime.session_for(intent)
            real_ready, real_mode = session.rehearse_readiness, session.rehearse_delivery_mode
            session.rehearse_readiness = lambda *a, _r=real_ready, **k: (
                rehearsals.append("readiness") or _r(*a, **k))
            session.rehearse_delivery_mode = lambda *a, _m=real_mode, **k: (
                rehearsals.append("mode") or _m(*a, **k))
            receipt, _event = self.dispatch(adapter, ledger, intent)
            self.assertEqual(receipt["outcome"], "succeeded", receipt)
        self.assertEqual(sorted(rehearsals), ["mode", "readiness"],
                         f"the rehearsals were repeated per dispatch: {rehearsals}")
        self.assertEqual(len(auth_probes), 3, "the volatile auth probe was cached")
        self.assertEqual(len(adapter.runtime.preflight_cache), 1)
        journal = self.journal("run_f11")
        spawned = [r for r in journal.rows() if r["event"] == "spawned"]
        decisions = [r["source_vocabulary"]["preflight_cache"] for r in spawned]
        self.assertEqual(decisions, ["miss", "hit", "hit"])
        self.assertEqual(spawned[1]["source_vocabulary"]["reused_checks"],
                         sorted(preflight_mod.CACHEABLE_CHECKS))
        self.assertEqual(spawned[1]["source_vocabulary"]["auth_check"], "refreshed")
        self.assertEqual(spawned[0]["source_vocabulary"]["preflight_fingerprint"],
                         spawned[2]["source_vocabulary"]["preflight_fingerprint"])

    def test_a_changed_binary_image_or_profile_misses_the_cache(self) -> None:
        adapter, _state, ledger = self._adapter("run_f11fp")
        session = adapter.runtime.session_for(self.intent("intent-f11fp", run_id="run_f11fp"))
        env = env_policy.build_child_env(session.profile, spawn_token="t1")
        base = preflight_mod.preflight_fingerprint(session.profile, env)
        self.assertEqual(preflight_mod.preflight_fingerprint(
            session.profile, env_policy.build_child_env(session.profile, spawn_token="t2")),
            base, "the per-spawn token changed the fingerprint")
        changed = session.profile.with_paths(worktree=self.worktree + "/other")
        self.assertNotEqual(preflight_mod.preflight_fingerprint(changed, env), base)
        binary = Path(shutil.which(session.profile.binary, path=env["PATH"]))
        stat = binary.stat()
        os.utime(binary, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        try:
            self.assertNotEqual(preflight_mod.preflight_fingerprint(session.profile, env), base,
                                "an upgraded binary image kept the cached preflight")
        finally:
            os.utime(binary, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    def test_a_failing_check_is_never_cached_and_auth_is_never_reused(self) -> None:
        self.assertNotIn("auth", preflight_mod.CACHEABLE_CHECKS)
        outcomes = preflight_mod.run_preflight(
            profile_from_mapping(stub_profile_spec("ready", worktree=self.worktree)),
            {"PATH": "/nonexistent"},
            reuse={"binary": {"verdict": "fail", "reason": "binary_absent", "evidence": {}},
                   "auth": {"verdict": "pass", "reason": "", "evidence": {}}})
        by_check = {o["check"]: o for o in outcomes}
        self.assertEqual(by_check["binary"]["verdict"], "fail")
        self.assertFalse(by_check["binary"]["evidence"].get("cached"))
        self.assertNotEqual(by_check["auth"]["verdict"], "pass")


if __name__ == "__main__":
    unittest.main()


# =====================================================================================
# CORRECTION ITERATION 2 -- CI failures on c7d19e2
# =====================================================================================
class CI2ExitEvidenceInFlightTests(_Composed):
    """CI-2.  `F11RefusedInterruptTests.test_a_refused_interrupt_does_not_make_the_row_block_the_pause`
    flaked (`PauseRefused: TERMINAL_ORPHAN_POSSIBLE … handle_recovery=not_listed`).

    Diagnosis (`evidence/iter2/ci2_diagnose_before.txt`, 18 of 40 reads under load): the
    agent had EXITED and was a zombie off its tty, its exit watcher was alive, and the fenced
    sentinel landed ~8-10 ms LATER -- the round-4 watcher polled `waitpid` every 50 ms, so
    `recover_handle` read the run inside that window and called a process that was proven
    ended a moment later an orphan.  Two fixes, each locked here: the watcher wakes on
    SIGCHLD (no window), and `recover_handle` treats "exited, watcher alive, no sentinel yet"
    as exit evidence IN FLIGHT and awaits it, bounded, rather than answering `not_listed`.
    """

    def _journal_a_spawned_intent(self, run_id: str, intent_id: str, *, pid: int,
                                  leader_pid: int, tty: str) -> tuple:
        adapter, _state, _ledger = self.compose_spec(
            stub_profile_spec("ready", worktree=self.worktree), run_id=run_id)
        journal = self.journal(run_id)
        session_id, incarnation = "sess-ci2", "inc-ci2"
        record = {"session_id": session_id, "process_incarnation": incarnation,
                  "pid": pid, "pgid": pid, "sid": leader_pid, "boot_id": "",
                  "proc_start_ticks": 0, "argv_digest": "d", "env_digest": "e",
                  "started_at": ""}
        pty_supervisor.write_spawn_record(
            pty_supervisor.spawn_record_path(self.base, run_id, intent_id, incarnation),
            record)
        for kind, event in (("EVENT", "spawned"), ("SPAWN_OBSERVED", "identity_bound")):
            journal.append(journal_mod.make_record(
                kind=kind, derived_from="pty", event=event, state="STARTING",
                intent_id=intent_id, dispatch_id="d", task_id="t", session_id=session_id,
                process_incarnation=incarnation,
                axes={"settlement": "not_settled", "worker_resource": "retain",
                      "process_liveness": "live", "cleanup_authority": "not_authorized"},
                source_vocabulary={"pty_id": "pty-ci2", "pid": pid, "captured_tty": tty,
                                   "session_digest": "d", "spawn_record": record,
                                   "terminal_role": "WORKER",
                                   "terminal_origin": "standalone_pty",
                                   "terminal_owner": f"{session_id}:{incarnation}",
                                   "agent_id": "a"}))
        sentinel = pty_supervisor.exit_sentinel_path(self.base, run_id, session_id, incarnation)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        return adapter, sentinel, f"{session_id}:{incarnation}"

    def _dead_pid(self) -> int:
        child = subprocess.Popen(["true"])
        child.wait()
        return child.pid

    def test_exit_evidence_in_flight_is_awaited_while_the_watcher_lives(self) -> None:
        """A real 'watcher' process writes the sentinel 300 ms from now; the agent pid is
        already gone.  `recover_handle` must answer `listing_verified` from the sentinel,
        not `not_listed` from the empty tty table."""
        agent = self._dead_pid()
        sentinel_holder: list = []
        watcher = os.fork()
        if watcher == 0:  # pragma: no cover - the fake watcher
            time.sleep(0.3)
            path = Path(self.base) / "runs" / "run_ci2" / "standalone" / "sess-ci2" / "exit.inc-ci2"
            pty_supervisor.write_exit_sentinel(path, code=0, fence="sess-ci2:inc-ci2")
            os._exit(0)
        self.addCleanup(lambda: kill_and_reap(watcher))
        adapter, sentinel, fence = self._journal_a_spawned_intent(
            "run_ci2", "intent-ci2", pid=agent, leader_pid=watcher, tty="ttys995")
        adapter._table_reader = lambda tty: {"tty": tty, "captured_at": time.time(),
                                             "rows": (), "readable": True}
        started = time.time()
        handle = adapter.recover_handle("intent-ci2")
        elapsed = time.time() - started
        self.assertEqual(handle["handle_recovery"], "listing_verified", handle)
        self.assertEqual(handle["exit_status"], 0)
        self.assertGreaterEqual(elapsed, 0.2, "the answer did not come from the awaited sentinel")
        self.assertLess(elapsed, 1.5)
        self.assertEqual(pty_supervisor.read_exit_sentinel(sentinel, fence=fence)["outcome"],
                         "exited")

    def test_a_zombie_on_the_tty_is_awaited_the_same_way(self) -> None:
        agent = self._dead_pid()
        watcher = os.fork()
        if watcher == 0:  # pragma: no cover
            time.sleep(0.2)
            path = Path(self.base) / "runs" / "run_ci2z" / "standalone" / "sess-ci2" / "exit.inc-ci2"
            pty_supervisor.write_exit_sentinel(path, code=3, fence="sess-ci2:inc-ci2")
            os._exit(0)
        self.addCleanup(lambda: kill_and_reap(watcher))
        adapter, _sentinel, _fence = self._journal_a_spawned_intent(
            "run_ci2z", "intent-ci2z", pid=agent, leader_pid=watcher, tty="ttys995")
        zombie = {"pid": agent, "ppid": watcher, "pgid": agent, "sid": watcher,
                  "tty": "ttys995", "stat": "Z+"}
        adapter._table_reader = lambda tty: {"tty": tty, "captured_at": time.time(),
                                             "rows": (zombie,), "readable": True}
        handle = adapter.recover_handle("intent-ci2z")
        self.assertEqual(handle["handle_recovery"], "listing_verified", handle)
        self.assertEqual(handle["exit_status"], 3)

    def test_a_gone_watcher_with_no_sentinel_is_still_an_orphan_and_waits_for_nothing(self) -> None:
        agent, watcher = self._dead_pid(), self._dead_pid()
        adapter, _sentinel, _fence = self._journal_a_spawned_intent(
            "run_ci2o", "intent-ci2o", pid=agent, leader_pid=watcher, tty="ttys995")
        adapter._table_reader = lambda tty: {"tty": tty, "captured_at": time.time(),
                                             "rows": (), "readable": True}
        started = time.time()
        handle = adapter.recover_handle("intent-ci2o")
        self.assertEqual(handle["handle_recovery"], "not_listed", handle)
        self.assertLess(time.time() - started, 0.5, "waited for a watcher that is gone")

    def test_a_wedged_watcher_is_unknown_never_verified(self) -> None:
        """The bound: the watcher lives but never writes -- the budget elapses and the
        answer is `listing_candidate` for a zombie row (unknown, never acted on) or
        `not_listed` for an absent one; never `listing_verified`."""
        agent = self._dead_pid()
        watcher = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: (watcher.kill(), watcher.wait()))
        adapter, _sentinel, _fence = self._journal_a_spawned_intent(
            "run_ci2w", "intent-ci2w", pid=agent, leader_pid=watcher.pid, tty="ttys995")
        adapter.EXIT_EVIDENCE_BUDGET_MS = 200
        zombie = {"pid": agent, "ppid": watcher.pid, "pgid": agent, "sid": watcher.pid,
                  "tty": "ttys995", "stat": "Z+"}
        adapter._table_reader = lambda tty: {"tty": tty, "captured_at": time.time(),
                                             "rows": (zombie,), "readable": True}
        handle = adapter.recover_handle("intent-ci2w")
        self.assertEqual(handle["handle_recovery"], "listing_candidate", handle)

    def test_the_watcher_wakes_on_sigchld_instead_of_polling(self) -> None:
        source = inspect.getsource(pty_supervisor._watch)
        self.assertIn("signal.set_wakeup_fd(wake_w", source)
        self.assertIn("signal.signal(signal.SIGCHLD", source)
        self.assertIn("select.select([guard_r, wake_r]", source)

    def test_the_real_spawn_leaves_no_unsentinelled_zombie_window_under_load(self) -> None:
        """The scenario that flaked, driven 15 times through the REAL spawn: the agent
        exits at once, and `recover_handle` is asked immediately afterwards.  Every answer
        must be `listing_verified` from the fenced sentinel."""
        spec = stub_profile_spec("complete-claude", worktree=self.worktree)
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_ci2real")
        outcomes = []
        for number in range(15):
            intent = self.intent(f"intent-ci2r-{number}", run_id="run_ci2real")
            session = adapter.runtime.session_for(intent)
            adapter._journal_planned(intent, session)
            claim = ledger.claim(intent)
            spawned = adapter.spawn_only(intent, lease_token=claim["lease_token"],
                                         payload="work", rehearsal=lambda p, e, s: {
                                             "channel": "structured", "record_type": "system",
                                             "session_id": s},
                                         mode_rehearsal=lambda p, e: {
                                             "r_b_closed": True, "delivery_proof": True,
                                             "auth_marker": None, "waited_without_prompt": True,
                                             "evaluable": True, "identity_bound": True,
                                             "detail": {}})
            self.assertEqual(spawned["start_outcome"], "ready", spawned)
            handle = adapter.recover_handle(intent["intent_id"])
            outcomes.append(handle["handle_recovery"])
            session.release()
        self.assertEqual(outcomes, ["listing_verified"] * 15, outcomes)


class CI1InterpreterImageResolutionTests(unittest.TestCase):
    """CI-1.  The wrapper refusal names the REAL interpreter image, resolved against the
    child PATH and `realpath`ed, on every platform (`/usr/bin/python3.12` on ubuntu)."""

    def test_env_shebang_resolves_through_the_child_path_and_realpath(self) -> None:
        room = Path(tempfile.mkdtemp(prefix="os37-ci1-"))
        self.addCleanup(shutil.rmtree, room, True)
        bindir = room / "bin"
        bindir.mkdir()
        real = bindir / "python3.99"
        real.write_text("#!/bin/sh\nexit 0\n")
        real.chmod(0o755)
        (bindir / "python3").symlink_to(real)
        wrapper = room / "wrapped"
        wrapper.write_text("#!/usr/bin/env python3\n")
        wrapper.chmod(0o755)
        found = preflight_mod.interpreter_wrapper(str(wrapper), {"PATH": str(bindir)})
        self.assertEqual(found["interpreter"], "/usr/bin/env")
        self.assertEqual(found["interpreter_image"], os.path.realpath(real))
        self.assertNotEqual(found["interpreter_image"], str(bindir / "python3"),
                            "the symlink, not the image, was reported")

    def test_an_env_name_the_child_cannot_resolve_is_reported_empty_not_fabricated(self) -> None:
        room = Path(tempfile.mkdtemp(prefix="os37-ci1c-"))
        self.addCleanup(shutil.rmtree, room, True)
        wrapper = room / "wrapped"
        wrapper.write_text("#!/usr/bin/env no-such-interpreter-os37\n")
        wrapper.chmod(0o755)
        found = preflight_mod.interpreter_wrapper(str(wrapper), {"PATH": str(room)})
        self.assertEqual(found["interpreter_image"], "")
        self.assertEqual(found["unresolved_interpreter"], "no-such-interpreter-os37")
        self.assertNotIn(os.getcwd(), found["interpreter_image"])

    def test_a_direct_shebang_is_realpathed_too(self) -> None:
        room = Path(tempfile.mkdtemp(prefix="os37-ci1b-"))
        self.addCleanup(shutil.rmtree, room, True)
        wrapper = room / "wrapped"
        wrapper.write_text("#!/bin/sh\n")
        wrapper.chmod(0o755)
        found = preflight_mod.interpreter_wrapper(str(wrapper), {"PATH": ""})
        self.assertEqual(found["interpreter_image"], os.path.realpath("/bin/sh"))
