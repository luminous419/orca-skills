"""OS-37 BUGFIX (run_ba46b3f9bc20): one lock per consolidated FOLLOW-UP review finding,
1-8, against head `11be83a` (issuecomment-5646243064).

Every test here FAILS (or ERRORS on an API the fix introduced) at `11be83a` and passes after
the fix.  The findings sit at the crash-recovery, pause-durability, bounded-resource and
launch-binding boundaries, so -- exactly as the review's acceptance clause requires --
findings 1-4 are driven with REAL processes and REAL crash boundaries: a supervisor that is
SIGKILLed mid-dispatch, an exit watcher that outlives it, an agent that keeps writing after
its supervisor is gone, a programming error raised over a live child.  The assertions read
DURABLE and OS-LEVEL state: the journal, the ledger, the exit sentinel, the capture file and
its integrity meta, the process table, and this process's own descriptor table.

The invariants the module locks, in the review's numbering:

  1. an in-flight standalone dispatch of a crashed supervisor is reconstructed from durable
     evidence, identity-fenced, and settled EXACTLY ONCE without re-spawning;
  2. a standalone pause is resumable END-TO-END with the original profile, ledger,
     approval authority and capabilities, and an unrecordable pause commits the SAME
     BLOCKED terminal it reports;
  3. every exception after spawn terminates/reaps/proves exit or durably RETAINS before
     a programming error may leave the runtime;
  4. the exit watcher's orphan drain honours the profile's capture limit, truncation state
     and integrity metadata, and metadata disagreement fails closed;
  5. the recorded runtime-state authority is create-once and exact-match;
  6. the dispatch-scoped result path is absolute before the child changes directory;
  7. a proven exit without a completion record settles at once;
  8. recovery restores the exact launch-time approval binding or refuses.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import os37_native_stub as native_stub  # noqa: E402
from scripts.deterministic_workflow import (executor, launcher,  # noqa: E402
                                            pause_runtime, pause_store,
                                            recovery_runtime, recovery_store,
                                            standalone_capture as capture_mod,
                                            standalone_journal as journal_mod,
                                            standalone_lifecycle as lifecycle,
                                            standalone_pty as pty_supervisor,
                                            standalone_runtime as runtime_mod)
from scripts.deterministic_workflow.runtime_state import (  # noqa: E402
    FileRuntimeStateStore, InMemoryRuntimeStateStore)
from scripts.deterministic_workflow.standalone_profile import (  # noqa: E402
    CaptureLimits, profile_from_mapping)
from scripts.test_os37_external_review_regressions import (  # noqa: E402
    GRAPH_CREDENTIAL_ENV, GRAPH_CREDENTIAL_VALUE, execute_graph_cli)
from scripts.test_os37_followup_review_regressions import (  # noqa: E402
    _Composed, _langgraph_ok, LANGGRAPH_REASON, agent_profile_spec, kill_and_reap,
    open_fds, pid_alive, stub_profile_spec, zombies_among)
from scripts.test_os37_lifecycle_boundary_regressions import (  # noqa: E402
    INJECTED_REHEARSALS)
from scripts.test_os37_pause_production_path import (  # noqa: E402
    REASON_CODE, _declare_blocked_source, _publish_open_decision)

REPO = Path(__file__).resolve().parent.parent

#: The REAL supervisor the crash cases SIGKILL.  A separate process because the crash must
#: be a crash: the agent, the exit watcher, the pty, the ledger lease and the execution
#: authority are all left exactly as a dead Coordinator leaves them.
SUPERVISOR = textwrap.dedent("""
    import json, sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from scripts.deterministic_workflow import launcher, recovery_store
    from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
    base, run_id, ledger_path, profile_path = (Path(sys.argv[2]), sys.argv[3],
                                               Path(sys.argv[4]), Path(sys.argv[5]))
    lease = float(sys.argv[6])
    ledger = FileRuntimeStateStore(ledger_path, lease_seconds=lease)
    profile = json.loads(profile_path.read_text())
    adapter, state = launcher.build_standalone_adapter(
        {"run_id": run_id, "thread_id": "t", "phases": ["DESIGN"], "max_iterations": 2},
        artifact_base=base, run_id=run_id, runtime_state=ledger, profile_spec=profile)
    checkpoint = launcher.resolve_checkpoint_path(run_id, "t", artifact_base=base)
    authority = recovery_store.FileRecoveryStateStore(
        recovery_store.authority_path_for_checkpoint(checkpoint), lease_seconds=lease)
    final = launcher.execute_state(
        state, adapter=adapter, runtime_state=ledger,
        journal=launcher._standalone_pause_row_journal(base, run_id),
        artifact_base=base, audit_sink=None, execution_authority=authority)
    print(json.dumps({"terminal_status": final.get("terminal_status")}))
""")


def compile_native_agent(room: Path, name: str, script_body: str) -> Path:
    """A NATIVE fixture whose behaviour is ``script_body``, through the reviewed C
    trampoline every other OS-37 fixture uses (R-A leg 4 reads the executable IMAGE).
    Returns the bin directory; the caller skips when no compiler is available."""
    tool = native_stub.compiler()
    if tool is None:                                       # pragma: no cover - CI has cc
        raise unittest.SkipTest(native_stub.NO_COMPILER_REASON)
    script = room / f"{name}.sh"
    script.write_text(script_body)
    script.chmod(0o755)
    bin_dir = room / f"{name}-bin"
    bin_dir.mkdir(exist_ok=True)
    target = bin_dir / name
    result = subprocess.run(
        [tool, "-O0", "-o", str(target), f'-DSTUB_SCRIPT="{script}"',
         str(native_stub.STUB_SRC)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    target.chmod(0o755)
    return bin_dir


def _ps_stat(pid: int) -> str:
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                         capture_output=True, text=True, check=False).stdout
    return out.strip()


def _wait_until(predicate, *, timeout: float, what: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


class _CrashRoom(unittest.TestCase):
    """A room with its own ledger directory, a worktree, and a REAL supervisor process."""

    LEASE_SECONDS = 3.0

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r5-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.previous = os.environ.get(launcher.RUNTIME_STATE_DIR_ENV)
        os.environ[launcher.RUNTIME_STATE_DIR_ENV] = str(self.base / "ledgers")
        self.addCleanup(self._restore_env)
        (self.base / "worktree").mkdir()
        self.supervisor: subprocess.Popen | None = None
        self.agent_pids: list[int] = []
        self.addCleanup(self._cleanup_processes)

    def _restore_env(self) -> None:
        if self.previous is None:
            os.environ.pop(launcher.RUNTIME_STATE_DIR_ENV, None)
        else:
            os.environ[launcher.RUNTIME_STATE_DIR_ENV] = self.previous

    def _cleanup_processes(self) -> None:
        if self.supervisor is not None:
            if self.supervisor.poll() is None:
                self.supervisor.kill()
                self.supervisor.wait(timeout=10)
            for stream in (self.supervisor.stdout, self.supervisor.stderr):
                if stream is not None:
                    stream.close()
        for pid in self.agent_pids:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)

    def launch(self, run_id: str, profile: dict) -> tuple[Path, Path]:
        ledger_path = launcher.default_runtime_state_path(run_id, "t")
        profile_path = self.base / f"{run_id}.profile.json"
        profile_path.write_text(json.dumps(profile))
        script = self.base / "supervisor.py"
        script.write_text(SUPERVISOR)
        self.supervisor = subprocess.Popen(
            [sys.executable, str(script), str(REPO), str(self.base), run_id,
             str(ledger_path), str(profile_path), str(self.LEASE_SECONDS)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ))
        return ledger_path, profile_path

    def journal(self, run_id: str) -> journal_mod.ExecutionJournal:
        return journal_mod.ExecutionJournal(self.base, run_id)

    def await_delivery(self, run_id: str) -> tuple[str, dict]:
        """Block until the Worker's prompt is PROVEN delivered; return (intent, spawned row)."""
        journal = self.journal(run_id)
        found: dict = {}

        def delivered() -> bool:
            if not journal.path.exists():
                return False
            for row in journal.rows():
                if row["event"] == "delivery_proof_observed":
                    found["intent_id"] = row["intent_id"]
                    return True
            return False
        _wait_until(delivered, timeout=60, what="the Worker's delivery proof")
        intent_id = found["intent_id"]
        spawned = next(row for row in journal.rows_for(intent_id)
                       if row["kind"] == "EVENT" and row["event"] == "spawned")
        self.agent_pids.append(int(spawned["source_vocabulary"]["pid"]))
        return intent_id, spawned

    def kill_supervisor(self) -> None:
        assert self.supervisor is not None
        os.kill(self.supervisor.pid, signal.SIGKILL)
        self.supervisor.wait(timeout=10)

    def recover(self, run_id: str) -> tuple[int, dict, str, BaseException | None]:
        out, err = io.StringIO(), io.StringIO()
        escaped: BaseException | None = None
        code = -1
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = launcher.run_watchdog_cli(
                    ["recover", "--run-id", run_id, "--artifact-base", str(self.base),
                     "--adapter", "standalone", "--json"])
        except BaseException as exc:            # noqa: BLE001 - the ESCAPE is the finding
            escaped = exc
        summary: dict = {}
        for line in reversed(out.getvalue().strip().splitlines()):
            try:
                summary = json.loads(line)
                break
            except ValueError:
                continue
        return code, summary, err.getvalue(), escaped


# =====================================================================================
# F1 -- a crashed supervisor's in-flight dispatch is collected, exactly once
# =====================================================================================
@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class F01CrashedSupervisorDispatchIsCollectedTests(_CrashRoom):
    """[P1] `resume()` only returned an EXISTING settlement row; a supervisor that died
    after the receipt left an effect the rebuilt runtime could not reconstruct or settle.
    """

    def _profile(self, **env: str) -> dict:
        return agent_profile_spec(
            worktree=str(self.base / "worktree"),
            driver_env={"OS37_GA_TURN_DELAY_MS": "4000",
                        "OS37_GA_TURN_DELAY_ROLE": "WORKER", **env},
            timeouts={"completion_timeout_ms": 30000})

    def test_the_in_flight_dispatch_is_collected_and_settled_exactly_once(self) -> None:
        run_id = "run_f1crash"
        ledger_path, _profile = self.launch(run_id, self._profile())
        intent_id, spawned = self.await_delivery(run_id)
        ledger = FileRuntimeStateStore(ledger_path)
        stored = ledger.get_receipt(intent_id)
        self.assertEqual(stored["status"], "EFFECTED")
        fence = stored["receipt"]["external_id"]
        agent_pid = int(spawned["source_vocabulary"]["pid"])
        self.kill_supervisor()                           # THE CRASH, mid-turn
        self.assertTrue(pid_alive(agent_pid), "the agent must outlive its supervisor")
        time.sleep(self.LEASE_SECONDS + 0.5)             # the dead owner's leases lapse
        fds_before = open_fds()
        code, summary, stderr, escaped = self.recover(run_id)
        self.assertIsNone(escaped, f"the recovery escaped as {escaped!r}")
        self.assertEqual(code, 0, f"{summary!r}\n{stderr}")
        self.assertEqual(summary.get("status"), "RECOVERED", summary)
        # -- settled EXACTLY ONCE, under the receipt's fence, from durable evidence -----
        rows = self.journal(run_id).rows_for(intent_id)
        settled = [row for row in rows if row["kind"] == "SETTLEMENT_OBSERVED"]
        self.assertEqual(len(settled), 1, [r["state"] for r in settled])
        self.assertEqual(f"{settled[0]['session_id']}:{settled[0]['process_incarnation']}",
                         fence, "the settlement is not fenced to the receipt")
        self.assertEqual(settled[0]["state"], "COMPLETED")
        adopted = [row for row in rows
                   if (row.get("source_vocabulary") or {}).get("adopted") is True
                   and row["event"] == "identity_bound"]
        self.assertEqual(len(adopted), 1, "the successor did not reconstruct the session")
        self.assertEqual(int(adopted[0]["source_vocabulary"]["pid"]), agent_pid,
                         "the adopted identity is not the spawned agent's")
        spawns = [row for row in rows if row["event"] == "spawned"]
        self.assertEqual(len(spawns), 1, "the recovery RE-SPAWNED the effect")
        ledger_settlement = ledger.get_settlement(intent_id)
        self.assertIsNotNone(ledger_settlement)
        self.assertEqual(ledger_settlement["event_id"],
                         settled[0]["source_vocabulary"]["event"]["event_id"],
                         "the ledger and the journal settled different events")
        # -- a second collection finds the row and adds nothing ------------------------
        adapter, _ledger, _journal = launcher._watchdog_wiring(argparse.Namespace(
            artifact_base=str(self.base), results="", adapter="standalone",
            run_owner="", project_root="", standalone_profile="")).adapter_for(run_id)
        again = adapter.resume({"intent_id": intent_id, "run_id": run_id},
                               dict(stored["receipt"]))
        self.assertEqual(again["event_id"], ledger_settlement["event_id"])
        self.assertEqual(len([r for r in self.journal(run_id).rows_for(intent_id)
                              if r["kind"] == "SETTLEMENT_OBSERVED"]), 1)
        # -- no live child, no zombie, no descriptor leak, bounded disk ----------------
        self.assertFalse(pid_alive(agent_pid), "the agent is still alive after settlement")
        leader = int(spawned["source_vocabulary"]["spawn_record"]["sid"]) \
            if spawned["source_vocabulary"].get("spawn_record") else 0
        self.assertEqual(zombies_among(agent_pid, leader), [])
        self.assertEqual(open_fds() - fds_before, set(), "descriptors leaked by recovery")
        session_dir = self.base / "runs" / run_id / "standalone" / fence.split(":")[0]
        capture = capture_mod.BoundedCapture(session_dir / "capture.log")
        self.assertLessEqual(capture.size, CaptureLimits().max_total_bytes)
        self.assertTrue(capture.integrity()["consistent"], capture.integrity())
        self.assertEqual(capture.writer, capture_mod.WRITER_EXIT_WATCHER,
                         "the exit watcher did not take over the capture")
        head = recovery_runtime.resolve_head(run_id, artifact_base=self.base)
        self.assertEqual(head.state.get("terminal_status"), "COMPLETED",
                         head.state.get("terminal_reason"))

    def test_a_dead_agent_with_no_sentinel_settles_typed_and_never_succeeds(self) -> None:
        """DR-2, collected: the watcher is SIGKILLed too, so no sentinel is ever written.
        The exit is then proven through the identity-fenced process table, the status is
        a NAMED absence, and the settlement is the typed failure -- never a success."""
        run_id = "run_f1nosent"
        ledger_path, _profile = self.launch(run_id, self._profile())
        intent_id, spawned = self.await_delivery(run_id)
        agent_pid = int(spawned["source_vocabulary"]["pid"])
        self.kill_supervisor()
        # The watcher (session leader) and the agent both die without a sentinel.
        probe = pty_supervisor.read_spawn_records(self.base, run_id, intent_id)
        leader = int(probe["record"]["sid"])
        for pid in (leader, agent_pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        _wait_until(lambda: not pid_alive(agent_pid) or _ps_stat(agent_pid).startswith("Z"),
                    timeout=10, what="the agent to die")
        time.sleep(self.LEASE_SECONDS + 0.5)
        code, summary, stderr, escaped = self.recover(run_id)
        self.assertIsNone(escaped, f"the recovery escaped as {escaped!r}")
        rows = self.journal(run_id).rows_for(intent_id)
        settled = [row for row in rows if row["kind"] == "SETTLEMENT_OBSERVED"]
        self.assertEqual(len(settled), 1, f"{summary!r}\n{stderr}")
        self.assertEqual(settled[0]["state"], "FAILED")
        self.assertNotEqual(settled[0]["outcome"], "succeeded")
        vocab = settled[0]["source_vocabulary"]
        self.assertIsNone(vocab.get("exit_status"), "a status was INVENTED for a lost exit")
        self.assertTrue(str(vocab.get("exit_proof", "")).startswith("process_table:"),
                        vocab.get("exit_proof"))
        self.assertEqual(len([row for row in rows if row["event"] == "spawned"]), 1)

    def test_adoption_is_refused_when_the_spawn_record_names_another_process(self) -> None:
        """Identity fencing: a spawn record that does not name the journal's pid is not
        this dispatch's evidence.  Nothing is adopted, nothing settled, and the refusal
        is durable."""
        run_id = "run_f1fence"
        ledger_path, _profile = self.launch(run_id, self._profile())
        intent_id, spawned = self.await_delivery(run_id)
        agent_pid = int(spawned["source_vocabulary"]["pid"])
        self.kill_supervisor()
        record_path = next((self.base / "runs" / run_id / "standalone" / "intents"
                            / intent_id).glob("spawn.*"))
        tampered = json.loads(record_path.read_text())
        tampered["pid"] = agent_pid + 100_000            # a stranger's pid
        record_path.write_text(json.dumps(tampered))
        stored = FileRuntimeStateStore(ledger_path).get_receipt(intent_id)
        adapter, _ledger, _journal = launcher._watchdog_wiring(argparse.Namespace(
            artifact_base=str(self.base), results="", adapter="standalone",
            run_owner="", project_root="", standalone_profile="")).adapter_for(run_id)
        event = adapter.resume({"intent_id": intent_id, "run_id": run_id},
                               dict(stored["receipt"]))
        self.assertIsNone(event)
        rows = self.journal(run_id).rows_for(intent_id)
        refused = [row for row in rows if row["kind"] == "REFUSED"
                   and (row.get("source_vocabulary") or {}).get("recovery")
                   == "adoption_refused"]
        self.assertEqual(len(refused), 1, "the refusal was not recorded durably")
        self.assertIn("do not name one process", refused[0]["source_vocabulary"]["detail"])
        self.assertEqual([r for r in rows if r["kind"] == "SETTLEMENT_OBSERVED"], [])
        self.assertTrue(pid_alive(agent_pid), "a refused adoption must signal nothing")


# =====================================================================================
# F2 -- standalone pause: durably recoverable and resumable end-to-end
# =====================================================================================
@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class F02UnrecordablePauseCommitsTheBlockedHeadTests(unittest.TestCase):
    """[P1] A pause record that could not be written left the committed head an orphaned
    WAITING_FOR_INPUT under a CLI that reported BLOCKED."""

    RUN = "run_f2orphan"

    def setUp(self) -> None:
        self.room = Path(tempfile.mkdtemp(prefix="os37-r5-f2a-"))
        self.addCleanup(shutil.rmtree, self.room, True)
        self.base = self.room / "artifact_base"
        self.base.mkdir(parents=True)
        key = _publish_open_decision(self.RUN, self.base)
        _declare_blocked_source(self.RUN, self.base, key)

    def test_the_reported_block_and_the_durable_head_are_the_same_statement(self) -> None:
        real_store_for = pause_store.store_for

        class NoSpace:
            def __init__(self, inner: object) -> None:
                self.inner = inner

            def create(self, record: object) -> None:
                raise OSError(28, "No space left on device")

            def __getattr__(self, name: str) -> object:
                return getattr(self.inner, name)

        pause_store.store_for = lambda run_id, **kw: NoSpace(real_store_for(run_id, **kw))
        try:
            run = execute_graph_cli(self.room, run_id=self.RUN,
                                    approval_authority="artifact",
                                    decision_state="NEEDS_INPUT",
                                    decision_reason_code=REASON_CODE)
        finally:
            pause_store.store_for = real_store_for
        self.assertIsNone(run.escaped)
        self.assertEqual(run.summary.get("terminal_status"), "BLOCKED", run.summary)
        self.assertEqual((run.summary.get("terminal_reason") or {}).get("code"),
                         launcher.PAUSE_RECORD_NOT_WRITTEN)
        # -- the committed head says the same thing --------------------------------
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        saver = FileCheckpointSaver(self.room / "checkpoints.json")
        stored = saver.get_tuple({"configurable": {"thread_id": "graph",
                                                   "checkpoint_ns": ""}})
        head = dict(stored.checkpoint["channel_values"])
        self.assertEqual(head.get("run_lifecycle"), "SETTLED", head.get("run_lifecycle"))
        self.assertEqual(head.get("terminal_status"), "BLOCKED")
        self.assertEqual((head.get("terminal_reason") or {}).get("code"),
                         launcher.PAUSE_RECORD_NOT_WRITTEN)
        self.assertEqual(list(pause_runtime.discover(self.base, langgraph_available=True)), [])
        authority = recovery_store.FileRecoveryStateStore(
            recovery_store.authority_path_for_checkpoint(self.room / "checkpoints.json"))
        self.assertEqual(authority.read(self.RUN)["status"], "SETTLED",
                         "a run whose head is terminal must seal its execution authority")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class F02StandalonePauseResumesEndToEndTests(unittest.TestCase):
    """[P1] `resume --adapter standalone` was refused while the default composed the FAKE
    adapter over a standalone run.  Now: one coherent contract -- a standalone-launched
    run resumes standalone, with its recorded profile, ledger and approval authority, and
    every other selection on it is refused by name."""

    RUN = "run_f2resume"

    def setUp(self) -> None:
        self.room = Path(tempfile.mkdtemp(prefix="os37-r5-f2b-"))
        self.addCleanup(shutil.rmtree, self.room, True)
        self.previous_dir = os.environ.get(launcher.RUNTIME_STATE_DIR_ENV)
        os.environ[launcher.RUNTIME_STATE_DIR_ENV] = str(self.room / "default-ledgers")
        self.addCleanup(self._restore_dir)
        self.base = self.room / "artifact_base"
        self.base.mkdir(parents=True)
        key = _publish_open_decision(self.RUN, self.base)
        _declare_blocked_source(self.RUN, self.base, key)
        self.paused = execute_graph_cli(self.room, run_id=self.RUN,
                                        approval_authority="artifact",
                                        decision_state="NEEDS_INPUT",
                                        decision_reason_code=REASON_CODE)
        self.assertIsNone(self.paused.escaped)
        self.assertEqual(self.paused.summary.get("run_lifecycle"), "WAITING_FOR_INPUT",
                         f"{self.paused.summary!r}\n{self.paused.stderr}")
        self.previous = os.environ.get(GRAPH_CREDENTIAL_ENV)
        os.environ[GRAPH_CREDENTIAL_ENV] = GRAPH_CREDENTIAL_VALUE
        self.addCleanup(self._restore_credential)

    def _restore_credential(self) -> None:
        if self.previous is None:
            os.environ.pop(GRAPH_CREDENTIAL_ENV, None)
        else:
            os.environ[GRAPH_CREDENTIAL_ENV] = self.previous

    def _restore_dir(self) -> None:
        if self.previous_dir is None:
            os.environ.pop(launcher.RUNTIME_STATE_DIR_ENV, None)
        else:
            os.environ[launcher.RUNTIME_STATE_DIR_ENV] = self.previous_dir

    def _answer(self) -> None:
        from scripts.clarification_protocol import (ArtifactHumanApprovalPort,
                                                    ResponseSubmission)
        port = ArtifactHumanApprovalPort(self.base)
        # The run's OWN clarification requests, under this case's temporary base -- the
        # OS-30 layout `execute_graph_cli` published into, never a committed run directory.
        root = self.base.joinpath("artifacts", "runs", self.RUN, "clarifications",
                                  "requests")
        for path in sorted(root.glob("request_*/record.json")):
            request = json.loads(path.read_text())
            for index, item in enumerate(request["items"]):
                option = (item["options"][0]["option_id"] if item.get("options")
                          else "staging")
                port.ingest(run_id=self.RUN, request_id=request["request_id"],
                            decision_item_id=item["decision_item_id"],
                            submission=ResponseSubmission(
                                f"sub_{index}", "alice", "human", "desk",
                                "2026-09-01T08:00:00Z", option, None, False, "normal"))

    def _resume(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = launcher.run_cli(["resume", "--run-id", self.RUN,
                                     "--artifact-base", str(self.base), "--json", *extra])
        return code, out.getvalue(), err.getvalue()

    def test_the_paused_run_resumes_with_its_recorded_bindings_and_really_dispatches(self) -> None:
        self._answer()
        journal = journal_mod.ExecutionJournal(self.base, self.RUN)
        before = len(journal.rows()) if journal.path.exists() else 0
        code, out, err = self._resume("--adapter", "standalone")
        self.assertEqual(code, 0, err)
        summary = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(summary.get("status"), "RESUMED", summary)
        rows = journal.rows()[before:]
        spawned = [row for row in rows if row["event"] == "spawned"]
        self.assertGreaterEqual(len(spawned), 1,
                                "the resumed round dispatched no real standalone agent")
        for row in spawned:
            self.assertFalse(pid_alive(int(row["source_vocabulary"]["pid"])))
        settled = [row for row in rows if row["kind"] == "SETTLEMENT_OBSERVED"]
        self.assertTrue(settled and all(r["state"] == "COMPLETED" for r in settled),
                        [r["state"] for r in settled])
        # The ORIGINAL ledger -- the one the launch recorded -- holds the settlements.
        binding = launcher.load_standalone_authority(self.base, self.RUN, "graph")
        self.assertEqual(Path(binding["runtime_state_path"]),
                         (self.room / "ledger.json").resolve())
        self.assertEqual(binding["approval_authority"], launcher.ARTIFACT_APPROVAL_AUTHORITY)
        ledger = FileRuntimeStateStore(self.room / "ledger.json")
        for row in settled:
            self.assertIsNotNone(ledger.get_settlement(row["intent_id"]),
                                 "the resumed round settled into another ledger")
        self.assertFalse(launcher.default_runtime_state_path(self.RUN, "graph").exists(),
                         "the resume opened the DEFAULT ledger beside the recorded one")
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        saver = FileCheckpointSaver(self.room / "checkpoints.json")
        stored = saver.get_tuple({"configurable": {"thread_id": "graph",
                                                   "checkpoint_ns": ""}})
        self.assertEqual(stored.checkpoint["channel_values"].get("terminal_status"),
                         "COMPLETED", stored.checkpoint["channel_values"].get("terminal_reason"))

    def test_the_fake_default_and_orca_are_refused_on_a_standalone_run(self) -> None:
        self._answer()
        for extra in ((), ("--adapter", "fake"), ("--adapter", "orca", "--run-owner", "x")):
            code, _out, err = self._resume(*extra)
            self.assertEqual(code, launcher.USAGE_EXIT_CODE, (extra, err))
            self.assertIn(launcher.STANDALONE_RUN_ADAPTER_MISMATCH, err, extra)
        record = pause_store.store_for(self.RUN, artifact_base=self.base).read(self.RUN)
        self.assertEqual(record["status"], "WAITING_FOR_INPUT",
                         "a refused selection must claim nothing and change nothing")


# =====================================================================================
# F3 -- no exception may leave a live child behind
# =====================================================================================
class F03ProgrammingErrorAfterSpawnTests(_Composed):
    """[P1] An exception outside the closed failure table -- a rotated lease refused inside
    `record_receipt` -- was re-raised by the adapter OVER A LIVE CHILD."""

    class _RotatedLedger(InMemoryRuntimeStateStore):
        """The receipt write refuses with an exception the failure table does not name."""

        def record_receipt(self, intent_id, receipt, lease_token):  # type: ignore[override]
            raise RuntimeError("LEASE_LOST:rotated-by-a-successor")

    def _alive_spec(self) -> dict:
        return stub_profile_spec(
            "alive", worktree=self.worktree,
            timeouts={"graceful_force_timeout_ms": 500, "force_retry_ms": 20,
                      "physical_exit_timeout_ms": 1500, "staleness_budget_ms": 5000})

    def _dispatch_raising(self, run_id: str, ledger):
        adapter, _state, ledger = self.compose_spec(self._alive_spec(), run_id=run_id,
                                                    ledger=ledger)
        session = adapter.runtime.session_for(self.intent("intent-f3", run_id=run_id))
        original = session.start

        def injected(**kwargs):
            kwargs.update(INJECTED_REHEARSALS)
            return original(**kwargs)
        session.start = injected                      # type: ignore[method-assign]
        intent = self.intent("intent-f3", run_id=run_id)
        claim = ledger.claim(intent)
        fds_before = open_fds()
        with self.assertRaises(RuntimeError) as caught:
            adapter.start(intent, lease_token=claim["lease_token"])
        return adapter, session, caught.exception, fds_before

    def test_the_child_is_proven_exited_and_reclaimed_before_the_error_escapes(self) -> None:
        adapter, session, error, fds_before = self._dispatch_raising(
            "run_f3", self._RotatedLedger())
        self.assertIn("LEASE_LOST", str(error), "the programming error must still escape")
        self.assertIsNotNone(session.record)
        pid = int(session.record["pid"])
        leader = int((session.pty or {}).get("leader_pid") or 0)
        self.assertFalse(pid_alive(pid), "the agent is still running after the error escaped")
        self.assertEqual(zombies_among(pid, leader), [], "a zombie was left behind")
        self.assertEqual(open_fds() - fds_before, set(), "the pty master leaked")
        rows = self.journal("run_f3").rows_for("intent-f3")
        secured = [row for row in rows
                   if (row.get("source_vocabulary") or {}).get("unexpected_error")]
        self.assertEqual(len(secured), 1, "no durable row names the escape")
        self.assertIn("RuntimeError", secured[0]["source_vocabulary"]["unexpected_error"])
        self.assertEqual(secured[0]["axes"]["process_liveness"], "already exited")
        self.assertEqual(secured[0]["source_vocabulary"]["settled"], False)
        self.assertEqual([r for r in rows if r["kind"] == "SETTLEMENT_OBSERVED"], [],
                         "an unnamed error must never settle a verdict")
        self.assertIn("intent-f3", adapter.open_dispatches(),
                      "the dispatch must stay OPEN so nothing starts beside it")

    def test_an_unprovable_exit_is_retained_by_name_and_the_error_still_escapes(self) -> None:
        """The process table cannot be read: the ladder refuses, nothing is signalled, the
        journal records RETAINED + unsettled, and the original error is what escapes."""
        unreadable = lambda tty: {"tty": tty, "captured_at": 0.0, "rows": (),  # noqa: E731
                                  "readable": False}
        ledger = self._RotatedLedger()
        adapter, _state, ledger = self.compose_spec(self._alive_spec(), run_id="run_f3ret",
                                                    ledger=ledger)
        adapter.runtime._session_kwargs["table_reader"] = unreadable
        session = adapter.runtime.session_for(self.intent("intent-f3r", run_id="run_f3ret"))
        original = session.start

        def injected(**kwargs):
            kwargs.update(INJECTED_REHEARSALS)
            return original(**kwargs)
        session.start = injected                      # type: ignore[method-assign]
        intent = self.intent("intent-f3r", run_id="run_f3ret")
        claim = ledger.claim(intent)
        with self.assertRaises(RuntimeError):
            adapter.start(intent, lease_token=claim["lease_token"])
        pid = int(session.record["pid"])
        self.assertTrue(pid_alive(pid), "an unownable process must not be signalled")
        rows = self.journal("run_f3ret").rows_for("intent-f3r")
        retained = [row for row in rows if (row.get("source_vocabulary") or {}).get("retained")]
        self.assertTrue(retained, "no durable RETAINED row was written")
        self.assertEqual(retained[-1]["axes"]["settlement"], "not_settled")
        self.assertEqual(retained[-1]["axes"]["worker_resource"], "retain")
        self.assertEqual([r for r in rows if r["kind"] == "SETTLEMENT_OBSERVED"], [])
        self.assertIn("intent-f3r", adapter.open_dispatches())


# =====================================================================================
# F4 -- the orphan drain honours the bounded-capture contract
# =====================================================================================
class F04OrphanDrainIsBoundedTests(unittest.TestCase):
    """[P1] The exit watcher appended raw bytes after supervisor death: a 4,096-byte limit
    retained 131,079 bytes and the capture still answered `answerable=True`."""

    SUPERVISOR = textwrap.dedent("""
        import json, os, sys, time
        sys.path.insert(0, sys.argv[1])
        from scripts.deterministic_workflow import standalone_capture as capture_mod
        from scripts.deterministic_workflow import standalone_pty as pty_supervisor
        from scripts.deterministic_workflow.standalone_profile import profile_from_mapping
        room, agent, handoff = sys.argv[2], sys.argv[3], sys.argv[4]
        profile = profile_from_mapping({
            "driver": "claude", "binary": "sh", "supported_range": [[1,0,0],[9,0,0]],
            "bin_dirs": ["/bin"], "worktree": room,
            "readiness_records": [{"channel": "structured", "record_type": "system",
                                   "session_field": "session_id"}],
            "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
            "identity_flag": "--session-id",
            "capture": {"max_total_bytes": 4096, "max_line_bytes": 4096}})
        base = os.path.join(room, "artifacts")
        sentinel = pty_supervisor.exit_sentinel_path(base, "run_f04", "sess", "inc1")
        os.makedirs(os.path.dirname(sentinel), exist_ok=True)
        capture = capture_mod.BoundedCapture(
            os.path.join(os.path.dirname(sentinel), "capture.log"), limits=profile.capture)
        session = pty_supervisor.spawn(
            argv=["/bin/sh", agent], env={"PATH": "/bin:/usr/bin"}, profile=profile,
            session_id="sess", incarnation="inc1",
            spawn_record_target=pty_supervisor.spawn_record_path(base, "run_f04", "i", "inc1"),
            cwd=room, sentinel=str(sentinel), fence="sess:inc1", image="/bin/sh")
        time.sleep(0.3)
        capture.append(os.read(session["master_fd"], 4096), at="t")
        with open(handoff, "w") as fh:
            json.dump({"agent_pid": session["pid"], "leader_pid": session["leader_pid"],
                       "sentinel": str(sentinel), "capture": str(capture.path)}, fh)
        os._exit(0)          # THE SUPERVISOR DIES; the agent then writes 128 KiB
    """)

    AGENT = textwrap.dedent("""\
        #!/bin/sh
        echo '{"type":"system","session_id":"x"}'
        sleep 1
        i=0
        while [ $i -lt 2048 ]; do
          printf '%064d\\n' "$i"
          i=$((i+1))
        done
        echo '{"type":"result","is_error":false}'
        exit 0
    """)

    def setUp(self) -> None:
        self.room = Path(tempfile.mkdtemp(prefix="os37-r5-f4-"))
        self.addCleanup(shutil.rmtree, self.room, True)

    def test_the_watcher_enforces_the_limit_and_the_capture_fails_closed(self) -> None:
        agent = self.room / "agent.sh"
        agent.write_text(self.AGENT)
        supervisor = self.room / "supervisor.py"
        supervisor.write_text(self.SUPERVISOR)
        handoff = self.room / "handoff.json"
        done = subprocess.run([sys.executable, str(supervisor), str(REPO), str(self.room),
                               str(agent), str(handoff)],
                              capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        info = json.loads(handoff.read_text())
        self.addCleanup(kill_and_reap, info["agent_pid"], info["leader_pid"])
        sentinel = Path(info["sentinel"])
        _wait_until(sentinel.exists, timeout=30, what="the exit sentinel")
        read = pty_supervisor.read_exit_sentinel(sentinel, fence="sess:inc1")
        self.assertEqual(read, {"outcome": "exited", "code": 0})
        capture_path = Path(info["capture"])
        # -- bounded disk: the limit held, and it is the profile's ---------------------
        self.assertLessEqual(capture_path.stat().st_size, 4096,
                             f"the orphan drain retained {capture_path.stat().st_size} bytes "
                             "against a 4,096-byte limit")
        meta = json.loads(capture_mod.meta_path_for(capture_path).read_text())
        self.assertEqual(meta["truncation"], capture_mod.TRUNCATION_TOTAL_BYTES)
        self.assertGreater(meta["dropped_bytes"], 100_000)
        self.assertEqual(meta["writer"], capture_mod.WRITER_EXIT_WATCHER)
        self.assertEqual(meta["total_bytes"], capture_path.stat().st_size)
        self.assertEqual(meta["sha256"], hashlib.sha256(capture_path.read_bytes()).hexdigest())
        # -- and a reader answers the same way the live supervisor would ---------------
        store = capture_mod.BoundedCapture(capture_path, limits=CaptureLimits(max_total_bytes=4096, max_line_bytes=4096))
        self.assertTrue(store.integrity()["consistent"], store.integrity())
        answer = store.completion_is_answerable()
        self.assertFalse(answer["answerable"], answer)
        self.assertEqual(answer["lost_reason"], capture_mod.CAPTURE_TRUNCATED_LOST_REASON)

    def test_metadata_disagreement_fails_closed(self) -> None:
        store = capture_mod.BoundedCapture(self.room / "capture.log")
        store.append(b'{"type":"result","is_error":false}\n', at="t")
        self.assertTrue(store.completion_is_answerable()["answerable"])
        with open(store.path, "ab") as handle:
            handle.write(b"appended outside the contract\n")
        reader = capture_mod.BoundedCapture(self.room / "capture.log")
        answer = reader.completion_is_answerable()
        self.assertFalse(answer["answerable"], answer)
        self.assertEqual(answer["lost_reason"], capture_mod.CAPTURE_INTEGRITY_LOST_REASON)
        self.assertEqual(answer["integrity"], "total_bytes_mismatch")
        self.assertIn(answer["lost_reason"], lifecycle.LOST_REASONS)
        # A file with bytes and NO meta is a disagreement too.
        bare = self.room / "bare.log"
        bare.write_bytes(b"x" * 10)
        self.assertEqual(capture_mod.BoundedCapture(bare).integrity()["reason"],
                         "meta_missing")
        # A rewritten tail with the same length is caught by the digest.
        store2 = capture_mod.BoundedCapture(self.room / "digest.log")
        store2.append(b"0123456789", at="t")
        with open(store2.path, "r+b") as handle:
            handle.seek(0)
            handle.write(b"9876543210")
        self.assertEqual(capture_mod.BoundedCapture(store2.path).integrity()["reason"],
                         "sha256_mismatch")

    def test_the_two_writers_make_the_same_limit_decision(self) -> None:
        """`admit_chunk` is the ONE decision both writers apply: the supervisor's store and
        the watcher's raw appender agree byte-for-byte on what reaches the file."""
        limits = CaptureLimits(max_total_bytes=300, max_line_bytes=100, max_records=5)
        chunks = [b"a" * 50, b"b" * 150, b"c" * 90, b"d" * 90, b"e" * 10, b"f" * 10, b"g"]
        store = capture_mod.BoundedCapture(self.room / "sup.log", limits=limits)
        for chunk in chunks:
            store.append(chunk, at="t")
        raw = capture_mod.RawBoundedAppender(os.fsencode(str(self.room / "raw.log")),
                                             limits=limits)
        for chunk in chunks:
            raw.append(chunk)
        raw.close()
        self.assertEqual((self.room / "sup.log").read_bytes(), (self.room / "raw.log").read_bytes())
        sup = json.loads((self.room / "sup.log.meta.json").read_text())
        wat = json.loads((self.room / "raw.log.meta.json").read_text())
        for key in ("records", "total_bytes", "dropped_bytes", "truncation", "sha256"):
            self.assertEqual(sup[key], wat[key], key)
        self.assertEqual(sup["writer"], capture_mod.WRITER_SUPERVISOR)
        self.assertEqual(wat["writer"], capture_mod.WRITER_EXIT_WATCHER)


# =====================================================================================
# F5 -- the recorded runtime-state authority is create-once, exact-match
# =====================================================================================
class F05AuthorityIsCreateOnceTests(unittest.TestCase):
    """[P1] `persist_standalone_authority` overwrote a different existing authority, so a
    re-invocation with another `--runtime-state` redirected recovery even when its own
    execution was rejected."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r5-f5-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.previous = os.environ.get(launcher.RUNTIME_STATE_DIR_ENV)
        os.environ[launcher.RUNTIME_STATE_DIR_ENV] = str(self.base / "ledgers")
        self.addCleanup(self._restore_env)
        (self.base / "worktree").mkdir()

    def _restore_env(self) -> None:
        if self.previous is None:
            os.environ.pop(launcher.RUNTIME_STATE_DIR_ENV, None)
        else:
            os.environ[launcher.RUNTIME_STATE_DIR_ENV] = self.previous

    def _compose(self, run_id: str, ledger_path: Path, approval_port: Any = None,
                 **spec_extra):
        ledger = FileRuntimeStateStore(ledger_path)
        return launcher.build_standalone_adapter(
            {"run_id": run_id, "thread_id": "t", "phases": ["DESIGN"], **spec_extra},
            artifact_base=self.base, run_id=run_id, runtime_state=ledger,
            profile_spec=agent_profile_spec(worktree=str(self.base / "worktree")),
            approval_port=approval_port)

    def _launch(self, run_id: str, ledger_path: Path, **spec_extra) -> dict:
        """A LAUNCH: compose, then `execute_state` up to (not into) the first dispatch.

        Correction 2: the launch bindings are published by `execute_state` after the
        run-scoped execution authority is claimed -- composing alone records nothing --
        so every case that needs a recorded binding gets it the way a real launch does.
        """
        adapter, state = self._compose(run_id, ledger_path, max_iterations=2, **spec_extra)
        stalled = launcher.execute_state(
            state, adapter=adapter, runtime_state=adapter.runtime_state,
            journal=launcher._standalone_pause_row_journal(self.base, run_id),
            artifact_base=self.base, interrupt_before=["EXECUTE_INTENT"], audit_sink=None)
        self.assertTrue(stalled.get("pending_intent"), stalled.get("terminal_reason"))
        return stalled

    def _authority_store(self, run_id: str, thread_id: str = "t"):
        checkpoint = launcher.resolve_checkpoint_path(run_id, thread_id,
                                                     artifact_base=self.base)
        return recovery_store.FileRecoveryStateStore(
            recovery_store.authority_path_for_checkpoint(checkpoint))

    def _standalone_root(self, run_id: str) -> Path:
        return launcher.standalone_profile_path(self.base, run_id).parent

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_a_different_ledger_is_refused_by_name_and_the_record_is_untouched(self) -> None:
        first = self.base / "one.json"
        self._launch("run_f5", first)
        path = launcher.standalone_authority_path(self.base, "run_f5", "t")
        before = path.read_bytes()
        with self.assertRaises(launcher.LauncherError) as caught:
            self._compose("run_f5", self.base / "two.json")
        self.assertIn(launcher.STANDALONE_AUTHORITY_CONFLICT, str(caught.exception))
        self.assertIn("runtime_state_path", str(caught.exception))
        self.assertEqual(path.read_bytes(), before, "the record was rewritten")
        recorded = launcher.load_standalone_authority(self.base, "run_f5", "t")
        self.assertEqual(Path(recorded["runtime_state_path"]), first.resolve())

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_an_identical_relaunch_is_a_restart_not_a_conflict(self) -> None:
        first = self.base / "one.json"
        self._launch("run_f5same", first)
        path = launcher.standalone_authority_path(self.base, "run_f5same", "t")
        mtime = path.stat().st_mtime_ns
        self._launch("run_f5same", first)                  # same ledger, same thread
        self.assertEqual(path.stat().st_mtime_ns, mtime, "an identical record was rewritten")

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_a_different_approval_authority_is_a_conflict_too(self) -> None:
        first = self.base / "one.json"
        self._launch("run_f5appr", first)
        with self.assertRaises(launcher.LauncherError) as caught:
            self._compose("run_f5appr", first,
                          approval_port=launcher.configured_approval_port(
                              launcher.ARTIFACT_APPROVAL_AUTHORITY, self.base))
        self.assertIn(launcher.STANDALONE_AUTHORITY_CONFLICT, str(caught.exception))
        self.assertIn("approval_authority", str(caught.exception))

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_a_rejected_relaunch_cannot_redirect_the_watchdog(self) -> None:
        """The exact scenario: a stalled run on ledger ONE; a second invocation names ledger
        TWO and is refused before it executes; the Watchdog still reopens ONE."""
        one = self.base / "one.json"
        adapter, state = self._compose("run_f5wd", one, max_iterations=2)
        stalled = launcher.execute_state(
            state, adapter=adapter, runtime_state=adapter.runtime_state,
            journal=launcher._standalone_pause_row_journal(self.base, "run_f5wd"),
            artifact_base=self.base, interrupt_before=["EXECUTE_INTENT"], audit_sink=None)
        self.assertTrue(stalled.get("pending_intent"))
        with self.assertRaises(launcher.LauncherError):
            self._compose("run_f5wd", self.base / "two.json", max_iterations=2)
        wiring = launcher._watchdog_wiring(argparse.Namespace(
            artifact_base=str(self.base), results="", adapter="standalone",
            run_owner="", project_root="", standalone_profile=""))
        _adapter, ledger, _journal = wiring.adapter_for("run_f5wd")
        self.assertEqual(ledger.path, one.resolve(),
                         "the rejected relaunch redirected recovery to its own ledger")

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_a_held_execution_authority_refuses_the_launch_before_any_binding_exists(self) -> None:
        """CORRECTION 2 (review section 5 / 11, G5): the absent-binding + held-authority race.

        Another Coordinator HOLDS the run's execution authority and no launch binding
        exists yet.  A first standalone launch of the same run naming a DIFFERENT
        `--runtime-state`, through the shipped command line, must be refused
        `EXECUTION_AUTHORITY_HELD` -- and must have created NOTHING: no binding, no
        profile, no archive.  The legitimate launch that follows records ITS ledger, and
        the Watchdog reopens exactly that.

        Mutation-sensitivity: publish at composition again (iteration 1's shape) and the
        refused invocation leaves `runtime_state.json` naming `intruder.json`, which the
        create-once rule then keeps forever -- the legitimate launch conflicts and the
        Watchdog is redirected.
        """
        run_id = "run_f5held"
        # -- another Coordinator owns the run ----------------------------------------
        holder = self._authority_store(run_id)
        held = holder.claim(run_id, thread_id="t", checkpoint_ns="",
                            now_iso=launcher._authority_now(),
                            owner_kind=recovery_store.OWNER_KIND_COORDINATOR,
                            takeover=False)
        self.assertEqual(held["claim_outcome"], recovery_store.CREATED)
        root = self._standalone_root(run_id)
        self.assertFalse(root.exists(), "nothing may exist before the first launch")
        # -- the intruding first launch, refused at the claim ------------------------
        profile = self.base / "profile.json"
        profile.write_text(json.dumps(agent_profile_spec(worktree=str(self.base / "worktree"))))
        state = self.base / "state.json"
        state.write_text(json.dumps({"run_id": run_id, "thread_id": "t",
                                     "phases": ["DESIGN"]}))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = launcher.run_cli([
                "--adapter", "standalone", "--state", str(state),
                "--standalone-profile", str(profile), "--artifact-base", str(self.base),
                "--runtime-state", str(self.base / "intruder.json"), "--json"])
        summary = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(summary.get("terminal_status"), "BLOCKED", (summary, err.getvalue()))
        self.assertEqual((summary.get("terminal_reason") or {}).get("code"),
                         recovery_store.EXECUTION_AUTHORITY_HELD, summary)
        self.assertEqual(code, 1)
        # -- and it published NOTHING --------------------------------------------------
        self.assertIsNone(launcher.load_standalone_authority(self.base, run_id, "t"),
                          "a refused launch created the create-once binding")
        self.assertIsNone(launcher.load_standalone_profile(self.base, run_id),
                          "a refused launch persisted a profile")
        self.assertEqual(sorted(p.name for p in root.rglob("*")) if root.exists() else [],
                         [], "a refused launch left files under the run's standalone root")
        self.assertEqual(holder.read(run_id)["lease_token"], held["lease_token"],
                         "the refused launch disturbed the holder's lease")
        # -- the legitimate launch, once the holder lets go -------------------------
        holder.release(run_id, held["lease_token"])
        legitimate = self.base / "one.json"
        self._launch(run_id, legitimate)
        recorded = launcher.load_standalone_authority(self.base, run_id, "t")
        self.assertIsNotNone(recorded)
        self.assertEqual(Path(recorded["runtime_state_path"]), legitimate.resolve())
        self.assertIsNotNone(launcher.load_standalone_profile(self.base, run_id))
        wiring = launcher._watchdog_wiring(argparse.Namespace(
            artifact_base=str(self.base), results="", adapter="standalone",
            run_owner="", project_root="", standalone_profile=""))
        _adapter, ledger, _journal = wiring.adapter_for(run_id)
        self.assertEqual(ledger.path, legitimate.resolve(),
                         "recovery is bound to something other than the legitimate ledger")
        self.assertFalse((self.base / "intruder.json").exists(),
                         "the refused launch opened its own ledger")

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_the_binding_is_published_after_the_claim_and_before_any_spawn(self) -> None:
        """The ordering itself, observed: composition alone records nothing, and a
        successful launch records the binding BEFORE its first dispatch executes (the
        graph is interrupted ahead of EXECUTE_INTENT and the binding is already there)."""
        one = self.base / "one.json"
        adapter, _state = self._compose("run_f5order", one)
        self.assertIsNone(launcher.load_standalone_authority(self.base, "run_f5order", "t"),
                          "composition published the binding before any claim")
        self.assertIsNone(launcher.load_standalone_profile(self.base, "run_f5order"))
        self.assertTrue(callable(getattr(adapter, "publish_launch_bindings", None)))
        self._launch("run_f5order", one)
        self.assertIsNotNone(launcher.load_standalone_authority(self.base, "run_f5order", "t"))
        self.assertFalse(journal_mod.ExecutionJournal(self.base, "run_f5order").path.exists(),
                         "a dispatch ran before the binding was checked")

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_through_the_shipped_command_line(self) -> None:
        """`run_workflow.py --adapter standalone --runtime-state <other>` on an existing
        run exits with the usage code and the conflict named, and executes nothing."""
        one = self.base / "one.json"
        self._launch("run_f5cli", one)
        profile = self.base / "profile.json"
        profile.write_text(json.dumps(agent_profile_spec(worktree=str(self.base / "worktree"))))
        state = self.base / "state.json"
        state.write_text(json.dumps({"run_id": "run_f5cli", "thread_id": "t",
                                     "phases": ["DESIGN"]}))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = launcher.run_cli([
                "--adapter", "standalone", "--state", str(state),
                "--standalone-profile", str(profile), "--artifact-base", str(self.base),
                "--runtime-state", str(self.base / "two.json"), "--json"])
        self.assertEqual(code, launcher.USAGE_EXIT_CODE, err.getvalue())
        self.assertIn(launcher.STANDALONE_AUTHORITY_CONFLICT, err.getvalue())
        self.assertFalse((self.base / "two.json").exists())
        self.assertFalse(journal_mod.ExecutionJournal(self.base, "run_f5cli").path.exists(),
                         "a refused relaunch must not execute")


# =====================================================================================
# F6 -- the result path is absolute before the child changes directory
# =====================================================================================
class F06ResultPathIsAbsoluteTests(_Composed):
    """[P2] The per-dispatch `-o` path was handed to the agent relative to ITS cwd (the
    worktree) and read back relative to the LAUNCHER's cwd: the file was written and
    `result_body()` returned `None`."""

    #: A CLI whose `-o` behaviour is the one this finding is about: it writes its final
    #: message to the path it was handed, relative to ITS OWN cwd.  Native (finding 10),
    #: through the same trampoline the other fixtures use.
    AGENT = textwrap.dedent("""\
        #!/bin/sh
        SESSION=""; OUT=""; WANT_VERSION=0; WANT_HELP=0; WANT_AUTH=0
        for arg in "$@"; do case "$arg" in login|auth) WANT_AUTH=1 ;; esac; done
        while [ $# -gt 0 ]; do
          case "$1" in
            --session-id) SESSION="$2"; shift 2 ;;
            -o) OUT="$2"; shift 2 ;;
            --version) WANT_VERSION=1; shift ;;
            --help) WANT_HELP=1; shift ;;
            *) shift ;;
          esac
        done
        if [ "$WANT_VERSION" = 1 ]; then echo "f6-agent 1.0.0"; exit 0; fi
        if [ "$WANT_HELP" = 1 ]; then
          echo "exec --json --ignore-user-config --ephemeral -C --add-dir"
          echo "--skip-git-repo-check --color -s -o --session-id"; exit 0; fi
        if [ "$WANT_AUTH" = 1 ]; then echo "Logged in"; exit 0; fi
        printf '{"type":"thread.started","thread_id":"%s"}\\n' "$SESSION"
        IFS= read -r PROMPT || PROMPT=""
        printf '{"type":"item.started","item":{"id":"item_0"}}\\n'
        if [ -n "$OUT" ]; then
          mkdir -p "$(dirname "$OUT")" 2>/dev/null
          printf 'F6 BODY written by the agent\\nSTATUS: COMPLETE\\n' > "$OUT"
        fi
        printf '{"type":"turn.completed","usage":{"input_tokens":1}}\\n'
        exit 0
    """)

    def _native_agent(self) -> Path:
        return compile_native_agent(self.base, "f6-agent", self.AGENT)

    def _spec(self, bin_dir: Path) -> dict:
        return {
            "driver": "codex", "binary": "f6-agent",
            "supported_range": [[0, 0, 0], [9, 0, 0]], "bin_dirs": [str(bin_dir)],
            "worktree": self.worktree,
            "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
            "identity_flag": "--session-id",
            "readiness_records": [{"channel": "structured", "record_type": "thread.started",
                                   "session_field": "thread_id"}],
            "delivery_proofs": [{"channel": "structured", "record_type": "item.started"}],
            "completion_records": [{"channel": "structured",
                                    "record_type": "turn.completed"}],
            # The body comes ONLY from the `-o` file: the record's own field is absent.
            "result_body_records": [{"channel": "structured", "record_type": "turn.completed",
                                     "body_field": "final_message"}],
            "output_last_message_path": "last.md",
            "auth_probe": {"args": ["login", "status"]},
            "timeouts": {"preflight_timeout_ms": 3000, "readiness_timeout_ms": 8000,
                         "delivery_verify_timeout_ms": 5000, "completion_timeout_ms": 8000},
        }

    def test_with_cwd_outside_the_worktree_and_a_relative_base_the_body_is_read(self) -> None:
        bin_dir = self._native_agent()
        launcher_cwd = self.base / "launcher-cwd"
        launcher_cwd.mkdir()
        previous = os.getcwd()
        os.chdir(launcher_cwd)                           # cwd != worktree, by design
        self.addCleanup(os.chdir, previous)
        relative = Path("rel-artifacts")
        ledger = InMemoryRuntimeStateStore()
        adapter, _state = launcher.build_standalone_adapter(
            {"run_id": "run_f6", "thread_id": "t", "phases": ["IMPLEMENTATION"]},
            artifact_base=relative, run_id="run_f6", runtime_state=ledger,
            profile_spec=self._spec(bin_dir))
        self.adapters.append(adapter)
        intent = self.intent("intent-f6", run_id="run_f6")
        session = adapter.runtime.session_for(intent)
        self.assertTrue(os.path.isabs(session.last_message_path),
                        f"the result path is relative: {session.last_message_path!r}")
        self.assertIn("-o", session.driver.argv(session_id="s"))
        argv = session.driver.argv(session_id="s")
        self.assertTrue(os.path.isabs(argv[argv.index("-o") + 1]))
        receipt, _event = self.dispatch(adapter, ledger, intent)
        self.assertEqual(receipt["outcome"], "succeeded", receipt)
        rows = [row for row in journal_mod.ExecutionJournal(relative, "run_f6")
                .rows_for("intent-f6") if row["kind"] == "SETTLEMENT_OBSERVED"]
        self.assertEqual(len(rows), 1)
        vocab = rows[0]["source_vocabulary"]
        self.assertEqual(vocab["result_body_source"], "output_last_message_path",
                         "the body written by the agent was not read back")
        provenance = vocab["result_body_provenance"]
        self.assertTrue(os.path.isabs(provenance["path"]), provenance)
        self.assertTrue(Path(provenance["path"]).is_file(), provenance)
        self.assertTrue(provenance["scoped_to_this_dispatch"])
        self.assertEqual([p.name for p in Path(self.worktree).rglob("last_message.*")], [],
                         "the agent wrote the result file inside its worktree")
        self.assertEqual(vocab["event"]["result"]["status"], "COMPLETE")


# =====================================================================================
# F7 -- a proven exit without a completion record settles at once
# =====================================================================================
class F07ProvenExitEndsTheWaitTests(_Composed):
    """[P2] `await_completion` broke only when BOTH a record and exit proof existed, so a
    crash spun for the whole completion budget re-parsing the transcript."""

    def test_a_process_that_exits_without_a_record_settles_well_inside_the_budget(self) -> None:
        marker = self.base / "once"
        spec = agent_profile_spec(
            worktree=self.worktree,
            driver_env={"OS37_GA_NO_RESULT_ONCE": str(marker),
                        "OS37_GA_NO_RESULT_ROLE": "WORKER"},
            timeouts={"completion_timeout_ms": 15000})
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f7")
        intent = self.intent("intent-f7", run_id="run_f7")
        started = time.monotonic()
        receipt, event = self.dispatch(adapter, ledger, intent)
        elapsed = time.monotonic() - started
        self.assertEqual(receipt["outcome"], "failed", receipt)
        rows = self.settlement_rows("run_f7", "intent-f7")
        self.assertEqual(len(rows), 1)
        verdict = rows[0]["source_vocabulary"]["completion_verdict"]
        # No record, exit 0, no exit-code map: the existing typed failure -- `lost` under
        # `exit_code_unmapped` -- exactly as before, only WITHOUT the budget-long wait.
        self.assertEqual(receipt.get("failure_stage"), "lost", receipt)
        self.assertEqual(verdict["reason"], "exit_code_unmapped", verdict)
        self.assertEqual(rows[0]["source_vocabulary"]["exit_status"], 0)
        self.assertEqual(rows[0]["source_vocabulary"]["exit_proof"], "exit_sentinel")
        self.assertLess(elapsed, 8.0,
                        f"the proven exit waited {elapsed:.1f}s of a 15s completion budget")
        self.assertIsNotNone(event)

    def test_the_break_is_on_exit_proof_and_still_drains_the_tail(self) -> None:
        """A session whose sentinel lands while the transcript's last bytes are still on
        the master: the record IS there after one more drain, and it settles COMPLETED."""
        spec = agent_profile_spec(worktree=self.worktree,
                                  timeouts={"completion_timeout_ms": 15000})
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f7tail")
        intent = self.intent("intent-f7t", run_id="run_f7tail")
        receipt, _event = self.dispatch(adapter, ledger, intent)
        self.assertEqual(receipt["outcome"], "succeeded", receipt)


# =====================================================================================
# F8 -- recovery restores the launch-time approval binding, or refuses
# =====================================================================================
@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class F08ApprovalBindingIsRestoredTests(unittest.TestCase):
    """[P2] The standalone watchdog reconstruction always supplied the artifact approval
    port, whatever the original `--approval-authority`."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r5-f8-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.previous = os.environ.get(launcher.RUNTIME_STATE_DIR_ENV)
        os.environ[launcher.RUNTIME_STATE_DIR_ENV] = str(self.base / "ledgers")
        self.addCleanup(self._restore_env)
        (self.base / "worktree").mkdir()

    def _restore_env(self) -> None:
        if self.previous is None:
            os.environ.pop(launcher.RUNTIME_STATE_DIR_ENV, None)
        else:
            os.environ[launcher.RUNTIME_STATE_DIR_ENV] = self.previous

    def _stall(self, run_id: str, authority: str) -> dict:
        ledger = FileRuntimeStateStore(launcher.default_runtime_state_path(run_id, "t"))
        adapter, state = launcher.build_standalone_adapter(
            {"run_id": run_id, "thread_id": "t", "phases": ["DESIGN"], "max_iterations": 2},
            artifact_base=self.base, run_id=run_id, runtime_state=ledger,
            profile_spec=agent_profile_spec(worktree=str(self.base / "worktree")),
            approval_port=launcher.configured_approval_port(authority, self.base))
        stalled = launcher.execute_state(
            state, adapter=adapter, runtime_state=ledger,
            journal=launcher._standalone_pause_row_journal(self.base, run_id),
            artifact_base=self.base, interrupt_before=["EXECUTE_INTENT"], audit_sink=None)
        self.assertTrue(stalled.get("pending_intent"))
        return state

    def _wiring(self):
        return launcher._watchdog_wiring(argparse.Namespace(
            artifact_base=str(self.base), results="", adapter="standalone",
            run_owner="", project_root="", standalone_profile=""))

    def test_a_run_launched_without_an_authority_is_recovered_without_one(self) -> None:
        state = self._stall("run_f8none", launcher.NO_APPROVAL_AUTHORITY)
        self.assertNotIn("human_approval", state["adapter_capabilities"])
        wiring = self._wiring()
        adapter, _ledger, _journal = wiring.adapter_for("run_f8none")
        self.assertIsNone(adapter.approval_port,
                          "recovery composed an approval authority the launch never had")
        self.assertNotIn("human_approval", adapter.capabilities())
        self.assertEqual(sorted(wiring["observation"]._capabilities("run_f8none")
                                if hasattr(wiring["observation"], "_capabilities") else
                                adapter.capabilities()),
                         sorted(state["adapter_capabilities"]),
                         "the recovered capability set differs from the launch snapshot")

    def test_a_run_launched_with_the_artifact_authority_is_recovered_with_it(self) -> None:
        state = self._stall("run_f8art", launcher.ARTIFACT_APPROVAL_AUTHORITY)
        self.assertIn("human_approval", state["adapter_capabilities"])
        adapter, _ledger, _journal = self._wiring().adapter_for("run_f8art")
        self.assertEqual(launcher.approval_authority_name(adapter.approval_port),
                         launcher.ARTIFACT_APPROVAL_AUTHORITY)
        self.assertEqual(sorted(adapter.capabilities()), sorted(state["adapter_capabilities"]))

    def test_an_unrebuildable_binding_refuses_recovery_by_name(self) -> None:
        self._stall("run_f8bad", launcher.NO_APPROVAL_AUTHORITY)
        path = launcher.standalone_authority_path(self.base, "run_f8bad", "t")
        record = json.loads(path.read_text())
        record["approval_authority"] = "unrecoverable:SomePort"
        path.write_text(json.dumps(record))
        with self.assertRaises(launcher.LauncherError) as caught:
            self._wiring().adapter_for("run_f8bad")
        self.assertIn(launcher.STANDALONE_APPROVAL_AUTHORITY_MISMATCH, str(caught.exception))

    def test_the_launch_records_the_binding_by_name(self) -> None:
        self._stall("run_f8rec", launcher.ARTIFACT_APPROVAL_AUTHORITY)
        record = launcher.load_standalone_authority(self.base, "run_f8rec", "t")
        self.assertEqual(record["approval_authority"], launcher.ARTIFACT_APPROVAL_AUTHORITY)
        self.assertEqual(record["adapter"], launcher.STANDALONE_ADAPTER)


# =====================================================================================
# Non-blocking note 1 -- `delivery_verify_timeout_ms` is configurable, and the boundary
# =====================================================================================
class NB1SlowFirstResponseBoundaryTests(_Composed):
    """The review's non-blocking note: the 15 s default may be aggressive for a slow first
    response.  The bound is a PROFILE field and was already configurable; this locks the
    boundary on a real process whose first response is deliberately slow, in both
    directions, so the default's meaning is measured rather than argued."""

    AGENT = textwrap.dedent("""\
        #!/bin/sh
        SESSION=""; WANT_VERSION=0; WANT_HELP=0; WANT_AUTH=0
        for arg in "$@"; do case "$arg" in auth) WANT_AUTH=1 ;; esac; done
        while [ $# -gt 0 ]; do
          case "$1" in
            --session-id) SESSION="$2"; shift 2 ;;
            --version) WANT_VERSION=1; shift ;;
            --help) WANT_HELP=1; shift ;;
            *) shift ;;
          esac
        done
        if [ "$WANT_VERSION" = 1 ]; then echo "slow-agent 1.0.0"; exit 0; fi
        if [ "$WANT_HELP" = 1 ]; then echo "--session-id -p --output-format"; exit 0; fi
        if [ "$WANT_AUTH" = 1 ]; then echo '{"loggedIn": true}'; exit 0; fi
        printf '{"type":"system","session_id":"%s"}\\n' "$SESSION"
        IFS= read -r PROMPT || PROMPT=""
        sleep "${OS37_SLOW_FIRST_RESPONSE_S:-0}"
        printf '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}\\n'
        printf '{"type":"result","is_error":false,"result":"STATUS: COMPLETE"}\\n'
        exit 0
    """)

    def _spec(self, bin_dir: Path, *, verify_ms: int, slow_s: float) -> dict:
        return {
            "driver": "claude", "binary": "slow-agent",
            "supported_range": [[0, 0, 0], [9, 0, 0]], "bin_dirs": [str(bin_dir)],
            "worktree": self.worktree,
            "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
            "identity_flag": "--session-id",
            "readiness_records": [{"channel": "structured", "record_type": "system",
                                   "session_field": "session_id"}],
            "delivery_proofs": [{"channel": "structured", "record_type": "assistant"}],
            "completion_records": [{"channel": "structured", "record_type": "result",
                                    "error_field": "is_error"}],
            "driver_env": {"OS37_SLOW_FIRST_RESPONSE_S": str(slow_s)},
            "auth_probe": {"args": ["auth", "status"]},
            "timeouts": {"preflight_timeout_ms": 3000, "readiness_timeout_ms": 8000,
                         "delivery_verify_timeout_ms": verify_ms,
                         "completion_timeout_ms": 15000},
        }

    def test_the_default_is_configurable_and_the_boundary_is_where_the_profile_says(self) -> None:
        bin_dir = compile_native_agent(self.base, "slow-agent", self.AGENT)
        # A first response 3 s late against a 1.5 s verification window: not delivered,
        # by the profile's own bound -- typed, never a hang.
        adapter, _state, ledger = self.compose_spec(
            self._spec(bin_dir, verify_ms=1500, slow_s=3.0), run_id="run_nb1short")
        receipt, _event = self.dispatch(adapter, ledger,
                                        self.intent("intent-nb1s", run_id="run_nb1short"))
        self.assertEqual(receipt["outcome"], "failed", receipt)
        self.assertEqual(receipt.get("failure_stage"), "delivery_not_observed", receipt)
        # The SAME slow response under a 8 s window: delivered and completed.
        adapter, _state, ledger = self.compose_spec(
            self._spec(bin_dir, verify_ms=8000, slow_s=3.0), run_id="run_nb1long")
        receipt, _event = self.dispatch(adapter, ledger,
                                        self.intent("intent-nb1l", run_id="run_nb1long"))
        self.assertEqual(receipt["outcome"], "succeeded", receipt)
        # And the default itself is what the profile module declares, unchanged.
        from scripts.deterministic_workflow.standalone_profile import Timeouts
        self.assertEqual(Timeouts().delivery_verify_timeout_ms, 15000)


if __name__ == "__main__":
    unittest.main()
