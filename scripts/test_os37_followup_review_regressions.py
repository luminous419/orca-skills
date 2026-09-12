"""OS-37 BUGFIX (run_193e35c17044): one mutation-sensitive lock per consolidated FOLLOW-UP
review finding, 1-19, against head `7ff47d7`.

Every test here FAILS (or ERRORS on an API the fix introduced) at `7ff47d7` and passes
after the fix.  Where a finding is about durable state the assertions read the journal,
the ledger, the exit sentinel, the process table and this process's own descriptor table
-- not the helper's return value alone -- because the review's own verification clause
asks for exactly that.

The four principles the fixes are held to:

* **Timeout is not settlement.**  A non-completing dispatch is settled `settled/release`
  only after its process's exit is PROVEN (terminate -> reap -> exit proven), and when
  that cannot be proven a durable RETAINED state is journalled and the run stops as a
  typed BLOCKED terminal so no subsequent work starts beside a possibly-live process.
* **Each semantic axis is read against itself**: the transport `SettlementEvent.outcome`,
  the journal's execution outcome, the workflow verdict, the identity fence and the
  incarnation are five different questions with five different answers.
* **The production composition paths are the paths under test**: launcher -> profile ->
  adapter -> graph, and watchdog discovery -> recovery -> a subsequent execution node.
* **Durable state, not return values.**
"""
from __future__ import annotations

import contextlib
import errno
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

from scripts import ci_lane, os37_graph_agent_fixture as graph_fixture  # noqa: E402
from scripts import os37_native_stub as native_stub  # noqa: E402
from scripts.deterministic_workflow import (contracts, executor,  # noqa: E402
                                            launcher, pause_policy, ports,
                                            recovery_runtime,
                                            standalone_capture as capture_mod,
                                            standalone_identity as identity,
                                            standalone_interrupt as interrupt_mod,
                                            standalone_journal as journal_mod,
                                            standalone_lifecycle as lifecycle,
                                            standalone_pty as pty_supervisor,
                                            standalone_runtime as runtime_mod)
from scripts.deterministic_workflow.runtime_state import (  # noqa: E402
    FileRuntimeStateStore, InMemoryRuntimeStateStore)
from scripts.deterministic_workflow.standalone_adapter import StandaloneAdapter  # noqa: E402
from scripts.deterministic_workflow.standalone_profile import (  # noqa: E402
    CaptureLimits, ProfileError, profile_from_mapping)
from scripts.test_os37_external_review_regressions import (  # noqa: E402
    STREAMS, _ProductionPath, execute_graph_cli, profile_spec, replay_profile)

REPO = Path(__file__).resolve().parent.parent


def _langgraph_ok() -> bool:
    try:
        import langgraph  # noqa: F401
    except ImportError:
        return False
    return True


LANGGRAPH_REASON = "LangGraph is not installed; the graph-route cases need the real graph"


def _stub_dir() -> Path:
    built = native_stub.native_stub_dir()
    if built is None:                                     # pragma: no cover - CI has cc
        raise AssertionError(native_stub.NO_COMPILER_REASON)
    return built


def _agent_dir() -> Path:
    built = graph_fixture.native_agent_dir()
    if built is None:                                     # pragma: no cover - CI has cc
        raise AssertionError(graph_fixture.NO_COMPILER_REASON)
    return built


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def open_fds() -> set[int]:
    return {int(entry) for entry in os.listdir("/dev/fd") if entry.isdigit()}


def kill_and_reap(*pids: int) -> None:
    """SIGKILL, then reap what is this process's child, so a cleanup leaves no zombie."""
    for pid in pids:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
    for pid in pids:
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                done, _status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                break
            if done == pid:
                break
            time.sleep(0.02)


def zombies_among(*pids: int) -> list[str]:
    """The given pids that are ZOMBIES right now -- scoped to what a case spawned, because
    a whole-suite run holds other suites' unreaped children in the same process."""
    wanted = [str(pid) for pid in pids if pid]
    if not wanted:
        return []
    out = subprocess.run(["ps", "-o", "pid=,stat=", "-p", ",".join(wanted)],
                         capture_output=True, text=True, check=False).stdout
    return [line.strip() for line in out.splitlines()
            if len(line.split()) >= 2 and "Z" in line.split()[1]]


def stub_profile_spec(mode: str, *, worktree: str, timeouts: dict | None = None,
                      extra_env: dict | None = None, exit_code_map: dict | None = None) -> dict:
    """A `post_ready_delivery` profile over the native stub, as an operator writes it."""
    spec = {
        "driver": "claude", "binary": "os37-stub-cli",
        "supported_range": [[1, 0, 0], [2, 0, 0]], "bin_dirs": [str(_stub_dir())],
        "worktree": worktree,
        "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
        "identity_flag": "--session-id",
        "driver_env": {"OS37_STUB_MODE": mode, "OS37_STUB_AUTH": "ok", **(extra_env or {})},
        "readiness_records": [{"channel": "structured", "record_type": "system",
                               "session_field": "session_id"}],
        "delivery_proofs": [{"channel": "structured", "record_type": "assistant"}],
        "completion_records": [{"channel": "structured", "record_type": "result",
                                "error_field": "is_error"}],
        "auth_probe": {"args": ["auth", "status"]},
        "timeouts": {"preflight_timeout_ms": 1500, "readiness_timeout_ms": 5000,
                     "delivery_verify_timeout_ms": 1500, "completion_timeout_ms": 1500,
                     "graceful_force_timeout_ms": 1000, "physical_exit_timeout_ms": 3000,
                     **(timeouts or {})},
    }
    if exit_code_map:
        spec["exit_code_map"] = exit_code_map
    return spec


def agent_profile_spec(*, worktree: str, driver_env: dict | None = None,
                       timeouts: dict | None = None, exit_code_map: dict | None = None) -> dict:
    spec = {
        "driver": "claude", "binary": "os37-graph-agent",
        "supported_range": [[1, 0, 0], [3, 0, 0]], "bin_dirs": [str(_agent_dir())],
        "worktree": worktree,
        "readiness_records": [{"channel": "structured", "record_type": "system",
                               "session_field": "session_id"}],
        "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
        "identity_flag": "--session-id",
        "delivery_proofs": [{"channel": "structured", "record_type": "assistant"}],
        "completion_records": [{"channel": "structured", "record_type": "result",
                                "error_field": "is_error", "success_field": "terminal_reason",
                                "success_values": ["completed"]}],
        "result_body_records": [{"channel": "structured", "record_type": "result",
                                 "body_field": "result"}],
        "driver_env": {"OS37_GA_BODY_IN_RESULT": "1", **(driver_env or {})},
        "auth_probe": {"args": ["auth", "status"]},
        "timeouts": {"preflight_timeout_ms": 1500, "readiness_timeout_ms": 10000,
                     "delivery_verify_timeout_ms": 5000, "completion_timeout_ms": 20000,
                     **(timeouts or {})},
    }
    if exit_code_map:
        spec["exit_code_map"] = exit_code_map
    return spec


def _with_injected_rehearsals(start):
    """Wrap `session.start` so the preflight REHEARSALS are injected (the stub's `agent`
    mode blocks on stdin, so a real rehearsal would spend its whole budget twice); every
    other preflight check and the spawn itself stay real."""
    import functools

    @functools.wraps(start)
    def wrapped(**kwargs):
        kwargs.setdefault("rehearsal", lambda p, e, s: {
            "channel": "structured", "record_type": "system", "session_id": s})
        kwargs.setdefault("mode_rehearsal", lambda p, e: {
            "r_b_closed": True, "delivery_proof": True, "auth_marker": None,
            "waited_without_prompt": False, "evaluable": True, "identity_bound": True,
            "detail": {"injected": "rehearsal"}})
        return start(**kwargs)
    return wrapped


class _Composed(_ProductionPath):
    """`build_standalone_adapter` over a JSON profile spec, exactly as `run_cli` composes."""

    def _reap_every_session(self) -> None:
        """The base cleanup signals the agent group; this one also reaps the exit WATCHER,
        which is this process's child and would otherwise sit as a zombie for the rest of
        the suite -- and the zombie assertions below would then blame the wrong case."""
        super()._reap_every_session()
        for adapter in self.adapters:
            runtime = getattr(adapter, "runtime", None)
            for session in list(getattr(runtime, "sessions", {}).values()):
                pty = session.pty or {}
                targets = [int(pty[key]) for key in ("pid", "leader_pid") if pty.get(key)]
                if targets:
                    kill_and_reap(*targets)

    def compose_spec(self, spec: dict, *, run_id: str, ledger=None):
        ledger = ledger if ledger is not None else InMemoryRuntimeStateStore()
        adapter, state = launcher.build_standalone_adapter(
            {"run_id": run_id, "thread_id": "t", "phases": ["IMPLEMENTATION"]},
            artifact_base=self.base, run_id=run_id, runtime_state=ledger,
            profile_spec=spec)
        self.adapters.append(adapter)
        return adapter, state, ledger

    def journal(self, run_id: str) -> journal_mod.ExecutionJournal:
        return journal_mod.ExecutionJournal(self.base, run_id)

    def settlement_rows(self, run_id: str, intent_id: str) -> list[dict]:
        return [row for row in self.journal(run_id).rows_for(intent_id)
                if row["kind"] == "SETTLEMENT_OBSERVED"]


# =====================================================================================
# F1 -- timeout is not settlement
# =====================================================================================
class F01TimeoutIsNotSettlementTests(_Composed):
    """[P1] A completion timeout settled and released a still-running process."""

    def test_a_timed_out_dispatch_is_terminated_reaped_and_proven_before_it_settles(self) -> None:
        """The child sleeps past every bound.  Before the fix `adapter.start` returned a
        `settled/release` settlement with `process_liveness=disputed` while the agent AND
        its exit watcher were both still alive."""
        spec = stub_profile_spec("ready-slow", worktree=self.worktree)
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f1")
        intent = self.intent("intent-f1", run_id="run_f1")
        fds_before = open_fds()
        receipt, event = self.dispatch(adapter, ledger, intent)
        session = adapter.runtime.session("intent-f1")
        pid, leader = int(session.record["pid"]), int(session.pty["leader_pid"])

        # -- the returned value -------------------------------------------------------
        self.assertEqual(receipt["outcome"], "failed")
        self.assertTrue(str(receipt.get("exit_proof", "")).startswith("interrupt_ladder:"),
                        f"the settlement was not preceded by a proven termination: "
                        f"{receipt!r}")
        self.assertEqual(receipt.get("teardown"), "proven")
        # -- the durable journal: settled, released, AND already exited --------------
        rows = self.settlement_rows("run_f1", "intent-f1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["axes"], {
            "settlement": "settled", "worker_resource": "release",
            "process_liveness": "already exited", "cleanup_authority": "authorized"})
        vocab = rows[0]["source_vocabulary"]
        self.assertEqual(vocab["teardown"], "proven")
        self.assertIn("exit_status", vocab)
        journal_rows = self.journal("run_f1").rows_for("intent-f1")
        ladder_rows = [row for row in journal_rows
                       if row["source_vocabulary"].get("interrupt_outcome")]
        self.assertTrue(ladder_rows, "no interrupt ladder was journalled before settlement")
        self.assertIn(ladder_rows[-1]["source_vocabulary"]["interrupt_outcome"],
                      ("interrupted_confirmed", "terminated_forced"))
        self.assertLess(ladder_rows[-1]["seq"], rows[0]["seq"],
                        "the termination must be journalled BEFORE the settlement")
        # -- the ledger ------------------------------------------------------------------
        self.assertIsNotNone(ledger.get_settlement("intent-f1"))
        self.assertEqual(event["result"]["status"], "BLOCKED")
        # -- process liveness, reaping and descriptors -----------------------------------
        self.assertFalse(pid_alive(pid), "the agent is still alive after settlement")
        self.assertFalse(pid_alive(leader), "the exit watcher is still alive")
        with self.assertRaises(ChildProcessError):
            os.waitpid(leader, os.WNOHANG)          # already reaped: nothing to collect
        self.assertEqual(zombies_among(pid, leader), [])
        self.assertEqual(session.pty["master_fd"], -1, "the master fd was not released")
        self.assertEqual(open_fds() - fds_before, set(), "a descriptor leaked")
        # -- the fenced exit sentinel: the watcher SURVIVED to write it (finding 12) ----
        sentinel = pty_supervisor.read_exit_sentinel(
            pty_supervisor.exit_sentinel_path(self.base, "run_f1", session.session_id,
                                              session.incarnation), fence=session.fence)
        self.assertEqual(sentinel["outcome"], "exited", sentinel)

    def test_an_unprovable_exit_is_retained_durably_and_blocks_instead_of_settling(self) -> None:
        """The ladder cannot see the process table, so the exit cannot be proven.

        Nothing settles: the journal holds a RETAINED state, the ledger stays EFFECTED,
        `open_dispatches` still lists the intent, and `adapter.start` raises the engine's
        own typed refusal rather than returning a settlement over a live process.
        """
        spec = stub_profile_spec("ready-slow", worktree=self.worktree)
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f1b")
        adapter.runtime._session_kwargs["table_reader"] = lambda tty: {
            "tty": tty, "captured_at": time.time(), "rows": (), "readable": False}
        intent = self.intent("intent-f1b", run_id="run_f1b")
        claim = ledger.claim(intent)
        with self.assertRaises(executor.IdempotencyRecoveryError) as refused:
            adapter.start(intent, lease_token=claim["lease_token"])
        self.assertEqual(refused.exception.code, "IDEMPOTENCY_RECOVERY_BLOCKED")
        self.assertIn("teardown_unproven", refused.exception.detail)
        session = adapter.runtime.session("intent-f1b")
        pid = int(session.record["pid"])
        try:
            self.assertTrue(pid_alive(pid),
                            "the fixture died on its own; this case needs a live process")
            rows = self.journal("run_f1b").rows_for("intent-f1b")
            self.assertEqual(self.settlement_rows("run_f1b", "intent-f1b"), [],
                             "a settlement was written over an unprovable exit")
            retained = rows[-1]
            self.assertEqual(retained["state"], "LOST")
            self.assertEqual(retained["lost_reason"], "stop_unverified")
            self.assertEqual(retained["axes"], {
                "settlement": "not_settled", "worker_resource": "retain",
                "process_liveness": "disputed", "cleanup_authority": "not_authorized"})
            self.assertTrue(retained["source_vocabulary"].get("retained"))
            self.assertIsNone(ledger.get_settlement("intent-f1b"))
            self.assertEqual(ledger.get_receipt("intent-f1b")["status"], "EFFECTED")
            self.assertIn("intent-f1b", adapter.open_dispatches())
            self.assertIsNone(adapter.settlement("intent-f1b"))
            self.assertNotEqual(session.pty["master_fd"], -1,
                                "a retained resource must not be released")
        finally:
            kill_and_reap(int(session.record["pid"]), int(session.pty["leader_pid"]))

    @unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
    def test_the_graph_stops_blocked_on_an_unprovable_exit_and_starts_no_more_work(self) -> None:
        """The same retained state, through the real graph: a typed BLOCKED terminal and
        exactly one spawn -- no correction round, no second dispatch."""
        spec = stub_profile_spec("ready-slow", worktree=self.worktree)
        ledger = InMemoryRuntimeStateStore()
        adapter, state, _ = self.compose_spec(spec, run_id="run_f1c", ledger=ledger)
        adapter.runtime._session_kwargs["table_reader"] = lambda tty: {
            "tty": tty, "captured_at": time.time(), "rows": (), "readable": False}
        final = launcher.execute_state(
            state, adapter=adapter, runtime_state=ledger, artifact_base=self.base,
            journal=adapter.pause_row_journal, audit_sink=None,
            require_durable_checkpointer=False)
        self.assertEqual(final["terminal_status"], "BLOCKED", final.get("terminal_reason"))
        self.assertEqual(final["terminal_reason"]["code"], "IDEMPOTENCY_RECOVERY_BLOCKED")
        spawned = [row for row in self.journal("run_f1c").rows()
                   if row["kind"] == "SPAWN_OBSERVED"]
        self.assertEqual(len(spawned), 1, "the graph started more work beside a live process")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class F01ReviewerTimeoutPrecedesCorrectionTests(unittest.TestCase):
    """A Reviewer that outruns its completion bound is terminated and reaped BEFORE the
    correction round its FAIL triggers may start, through the real `run_workflow.py`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.room = Path(tempfile.mkdtemp(prefix="os37-f1-reviewer-"))
        cls.fds_before = open_fds()
        cls.outcome = execute_graph_cli(
            cls.room, run_id="run_f1reviewer", phases=("DESIGN",), max_iterations=2,
            driver_env={"OS37_GA_TURN_DELAY_MS": "6000",
                        "OS37_GA_TURN_DELAY_ROLE": "PHASE_REVIEWER"},
            timeouts={"completion_timeout_ms": 2000, "graceful_force_timeout_ms": 1000,
                      "physical_exit_timeout_ms": 3000})

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.room, ignore_errors=True)

    def test_the_reviewer_is_proven_dead_before_the_next_dispatch_is_journalled(self) -> None:
        """Round 3 asserted the timed-out Reviewer was proven dead and reclaimed BEFORE the
        correction round its typed `FAIL` opened.  Round 4 (finding 6) removes that
        correction round altogether -- a timeout is a runtime failure, not a review
        verdict -- so the same case now asserts the process facts unchanged (proven dead,
        reaped, reclaimed, no leak) and that NO later dispatch of any kind follows: the
        run stops as the typed `REVIEWER_RUNTIME_FAILURE` terminal."""
        self.assertIsNone(self.outcome.escaped, f"the run escaped: {self.outcome.escaped!r}")
        rows = self.outcome.journal_rows()
        failures = [row for row in rows if row["kind"] == "EVENT"
                    and (row["source_vocabulary"].get("runtime_failure") or {})
                    .get("stage") == "lost"]
        self.assertTrue(failures, "no reviewer dispatch timed out; the case is vacuous")
        first = failures[0]
        self.assertEqual(first["source_vocabulary"]["code"], "REVIEWER_RUNTIME_FAILURE")
        self.assertEqual(first["axes"]["process_liveness"], "already exited")
        self.assertEqual(first["axes"]["cleanup_authority"], "authorized")
        self.assertEqual(first["axes"]["settlement"], "not_settled")
        self.assertEqual([row for row in rows if row["kind"] == "SETTLEMENT_OBSERVED"
                          and row["intent_id"] == first["intent_id"]], [],
                         "the timed-out reviewer was SETTLED; a timeout is not a verdict")
        reclaim = [row for row in rows if row["intent_id"] == first["intent_id"]
                   and row["source_vocabulary"].get("master_fd_closed")]
        self.assertTrue(reclaim, "the timed-out reviewer's pty was never reclaimed")
        self.assertTrue(reclaim[0]["source_vocabulary"]["leader_reaped"])
        self.assertLess(reclaim[0]["seq"], first["seq"],
                        "the failure was journalled before the process was reclaimed")
        later_intents = [row for row in rows if row["kind"] == "DELIVERY_INTENT"
                         and row["seq"] > first["seq"]]
        self.assertEqual(later_intents, [],
                         "a correction round followed a reviewer TIMEOUT (finding 6)")
        self.assertEqual(self.outcome.summary.get("terminal_status"), "BLOCKED")
        self.assertEqual((self.outcome.summary.get("terminal_reason") or {}).get("code"),
                         "REVIEWER_RUNTIME_FAILURE")
        pid = int(first["source_vocabulary"]["pid"])
        leader = int(reclaim[0]["source_vocabulary"]["leader_pid"])
        self.assertFalse(pid_alive(pid), "the timed-out reviewer is still alive")
        self.assertEqual(zombies_among(pid, leader), [])
        self.assertEqual(open_fds() - self.fds_before, set(), "a descriptor leaked")


# =====================================================================================
# F2 -- resume returns the row it validated
# =====================================================================================
class F02ResumeReturnsTheValidatedRowTests(unittest.TestCase):

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)
        self.journal = journal_mod.ExecutionJournal(self.base, "run_f2")

    def _row(self, session: str, incarnation: str, event_id: str) -> dict:
        return journal_mod.make_record(
            kind="SETTLEMENT_OBSERVED", derived_from="runtime_state",
            intent_id="intent-f2", dispatch_id=f"d-{incarnation}", task_id="t",
            session_id=session, process_incarnation=incarnation,
            event="settlement_confirmed", state="COMPLETED", outcome="succeeded",
            message_id=event_id, reported_by=f"{session}:{incarnation}",
            axes={"settlement": "settled", "worker_resource": "release",
                  "process_liveness": "already exited", "cleanup_authority": "authorized"},
            source_vocabulary={"event": {"event_id": event_id, "outcome": "SUCCEEDED",
                                         "result": {"status": "COMPLETE",
                                                    "from": f"{session}:{incarnation}"}}})

    def test_a_matching_row_followed_by_a_foreign_row_returns_the_matching_event(self) -> None:
        self.journal.append(self._row("s-A", "i-A", "event_A"))
        self.journal.append(self._row("s-B", "i-B", "event_B"))
        adapter = StandaloneAdapter(None, settlement_journal=self.journal,
                                    artifact_base=self.base, run_id="run_f2")
        got = adapter.resume({"intent_id": "intent-f2"}, {"external_id": "s-A:i-A"})
        self.assertIsNotNone(got)
        self.assertEqual(got["event_id"], "event_A")
        self.assertEqual(got["result"]["from"], "s-A:i-A")
        got_b = adapter.resume({"intent_id": "intent-f2"}, {"external_id": "s-B:i-B"})
        self.assertEqual(got_b["event_id"], "event_B")
        self.assertIsNone(adapter.resume({"intent_id": "intent-f2"},
                                         {"external_id": "s-C:i-C"}))

    def test_a_ledger_that_settled_a_different_event_is_refused_not_resolved(self) -> None:
        self.journal.append(self._row("s-A", "i-A", "event_A"))
        ledger = InMemoryRuntimeStateStore()
        intent = {"intent_id": "intent-f2", "command_id": "c", "payload_digest": "d",
                  "run_id": "run_f2", "phase": "IMPLEMENTATION", "role": "WORKER",
                  "round_kind": "PHASE_GATE", "action_kind": "DISPATCH_AGENT"}
        claim = ledger.claim(intent)
        ledger.record_receipt("intent-f2", {"intent_id": "intent-f2", "task_id": "t",
                                            "dispatch_id": "d", "external_id": "s-A:i-A"},
                              claim["lease_token"])
        other = contracts.make_settlement_event(intent, {"status": "BLOCKED"},
                                                occurred_at="1970-01-01T00:00:00Z")
        self.assertNotEqual(other["event_id"], "event_A")
        ledger.settle("intent-f2", other, claim["lease_token"])
        adapter = StandaloneAdapter(None, runtime_state=ledger,
                                    settlement_journal=self.journal,
                                    artifact_base=self.base, run_id="run_f2")
        self.assertIsNone(adapter.resume(intent, {"external_id": "s-A:i-A"}),
                          "a ledger that settled a different event was reconciled by guess")


# =====================================================================================
# F3 -- the spawn record is written after every fallible pre-exec step
# =====================================================================================
class F03SpawnRecordAfterChdirTests(unittest.TestCase):

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)
        self.profile = profile_from_mapping(
            stub_profile_spec("ready", worktree=str(self.base)))
        self.env = {"PATH": f"{_stub_dir()}:/usr/bin:/bin", "OS37_STUB_MODE": "ready",
                    "HOME": os.environ.get("HOME", "/")}

    def test_a_missing_worktree_forks_nothing_and_writes_no_spawn_record(self) -> None:
        missing = str(self.base / "does-not-exist")
        target = pty_supervisor.spawn_record_path(self.base, "run_f3", "intent-f3", "i-1")
        with self.assertRaises(OSError) as refused:
            pty_supervisor.spawn(
                argv=("os37-stub-cli", "--session-id", "s-1"), env=self.env,
                profile=self.profile, session_id="s-1", incarnation="i-1",
                spawn_record_target=str(target), cwd=missing,
                sentinel=str(self.base / "exit.i-1"), fence="s-1:i-1")
        self.assertEqual(refused.exception.errno, errno.ENOENT)
        probe = pty_supervisor.read_spawn_records(self.base, "run_f3", "intent-f3")
        self.assertEqual(probe["outcome"], "absent", probe)
        self.assertFalse((self.base / "exit.i-1").exists(),
                         "an exit watcher ran, so a child was forked before the check")
        # And a retry is NOT idempotency-blocked: `lookup` proves absence.
        adapter = StandaloneAdapter(None, settlement_journal=journal_mod.ExecutionJournal(
            self.base, "run_f3"), artifact_base=self.base, run_id="run_f3")
        self.assertIsNone(adapter.lookup({"intent_id": "intent-f3"}))

    def test_a_missing_image_forks_nothing(self) -> None:
        target = pty_supervisor.spawn_record_path(self.base, "run_f3", "intent-img", "i-1")
        with self.assertRaises(OSError):
            pty_supervisor.spawn(
                argv=("no-such-binary-anywhere",), env=self.env, profile=self.profile,
                session_id="s-1", incarnation="i-1", spawn_record_target=str(target),
                cwd=str(self.base))
        self.assertEqual(pty_supervisor.read_spawn_records(self.base, "run_f3",
                                                           "intent-img")["outcome"], "absent")

    def test_chdir_precedes_the_spawn_record_in_the_child(self) -> None:
        """STATIC: every fallible pre-exec operation the child performs comes BEFORE the
        record, and the record is still the last write before `execve`."""
        source = textwrap.dedent(inspect.getsource(pty_supervisor.spawn))
        child = source.split("if agent_pid == 0:", 1)[1]
        self.assertLess(child.index("os.chdir("), child.index("write_spawn_record("),
                        "chdir must precede the spawn record: a missing worktree exits 127 "
                        "without reaching execve while a record says an execve was reached")
        self.assertLess(child.index("write_spawn_record("), child.index("os.execve("))


# =====================================================================================
# F4 -- one axis against itself
# =====================================================================================
class F04ContradictionReadsOneAxisTests(unittest.TestCase):

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)
        self.journal = journal_mod.ExecutionJournal(self.base, "run_f4")
        self.ledger = InMemoryRuntimeStateStore()
        self.intent = {"intent_id": "intent-f4", "command_id": "c", "payload_digest": "d",
                       "run_id": "run_f4", "phase": "IMPLEMENTATION", "role": "WORKER",
                       "round_kind": "PHASE_GATE", "action_kind": "DISPATCH_AGENT"}
        self.claim = self.ledger.claim(self.intent)
        self.ledger.record_receipt("intent-f4", {"intent_id": "intent-f4", "task_id": "t",
                                                 "dispatch_id": "d", "external_id": "s:i"},
                                   self.claim["lease_token"])

    def _row(self, outcome: str, message_id: str, event: dict) -> dict:
        return journal_mod.make_record(
            kind="SETTLEMENT_OBSERVED", derived_from="runtime_state",
            intent_id="intent-f4", dispatch_id="d", task_id="t", session_id="s",
            process_incarnation="i", event="settlement_confirmed",
            state="FAILED" if outcome == "failed" else "COMPLETED", outcome=outcome,
            message_id=message_id, reported_by="s:i",
            axes={"settlement": "settled", "worker_resource": "release",
                  "process_liveness": "already exited", "cleanup_authority": "authorized"},
            source_vocabulary={"event": dict(event)})

    def test_a_typed_failed_settlement_admits_its_failed_row_and_refuses_a_succeeded_one(self) -> None:
        failed = contracts.make_settlement_event(
            self.intent, lifecycle.typed_failed_result(
                {"status": "COMPLETE"}, role="WORKER",
                verdict={"outcome": "failed", "reason": "exit_code_nonzero"}),
            occurred_at="1970-01-01T00:00:00Z")
        self.assertEqual(failed["outcome"], "SUCCEEDED",
                         "the frozen transport vocabulary must not have been changed")
        self.ledger.settle("intent-f4", failed, self.claim["lease_token"])
        legit = self.journal.admit(self._row("failed", failed["event_id"], failed),
                                   runtime_state=self.ledger)
        self.assertEqual(legit["outcome"], "admitted", legit)
        contradiction = self.journal.admit(
            self._row("succeeded", "event_other",
                      contracts.make_settlement_event(self.intent, {"status": "COMPLETE"},
                                                      occurred_at="1970-01-01T00:00:00Z")),
            runtime_state=self.ledger)
        self.assertEqual(contradiction["outcome"], "refused")
        self.assertEqual(contradiction["code"], journal_mod.SETTLEMENT_CONFLICT)

    def test_a_succeeded_settlement_refuses_a_failed_row_and_a_different_verdict(self) -> None:
        passed = contracts.make_settlement_event(self.intent, {"status": "COMPLETE"},
                                                 occurred_at="1970-01-01T00:00:00Z")
        self.ledger.settle("intent-f4", passed, self.claim["lease_token"])
        failed_row = self.journal.admit(
            self._row("failed", "event_f", contracts.make_settlement_event(
                self.intent, lifecycle.typed_failed_result(
                    {}, role="WORKER", verdict={"outcome": "failed"}),
                occurred_at="1970-01-01T00:00:00Z")),
            runtime_state=self.ledger)
        self.assertEqual(failed_row["code"], journal_mod.SETTLEMENT_CONFLICT)
        # The WORKFLOW verdict is its own axis: same execution outcome, different verdict.
        other_verdict = self.journal.admit(
            self._row("succeeded", "event_v", contracts.make_settlement_event(
                self.intent, {"status": "BLOCKED"}, occurred_at="1970-01-01T00:00:00Z")),
            runtime_state=self.ledger)
        self.assertEqual(other_verdict["code"], journal_mod.SETTLEMENT_CONFLICT)
        same = self.journal.admit(self._row("succeeded", passed["event_id"], passed),
                                  runtime_state=self.ledger)
        self.assertEqual(same["outcome"], "admitted", same)

    def test_the_frozen_event_contract_was_not_edited_to_fix_this(self) -> None:
        source = inspect.getsource(contracts.make_settlement_event)
        self.assertIn('"outcome": "SUCCEEDED"', source)
        self.assertEqual(lifecycle.execution_outcome_of(
            lifecycle.typed_failed_result({}, role="WORKER", verdict={})), "failed")
        self.assertEqual(lifecycle.execution_outcome_of({"status": "COMPLETE"}), "succeeded")
        self.assertEqual(lifecycle.execution_outcome_of(None), "")


# =====================================================================================
# F5 -- watchdog discovery -> recovery -> a subsequent execution node
# =====================================================================================
@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class F05WatchdogRecoversAStalledStandaloneRunTests(unittest.TestCase):
    """[P1] The watchdog bound the recovered graph to `StandaloneAdapter(None, ...)`."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-f5-"))
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

    def _stall(self, run_id: str) -> tuple[dict, FileRuntimeStateStore]:
        ledger = FileRuntimeStateStore(launcher.default_runtime_state_path(run_id, "t"))
        adapter, state = launcher.build_standalone_adapter(
            {"run_id": run_id, "thread_id": "t", "phases": ["DESIGN"], "max_iterations": 2},
            artifact_base=self.base, run_id=run_id, runtime_state=ledger,
            profile_spec=agent_profile_spec(worktree=str(self.base / "worktree")))
        stalled = launcher.execute_state(
            state, adapter=adapter, runtime_state=ledger,
            journal=launcher._standalone_pause_row_journal(self.base, run_id),
            artifact_base=self.base, interrupt_before=["EXECUTE_INTENT"], audit_sink=None)
        self.assertIsNone(stalled.get("terminal_status"))
        self.assertTrue(stalled.get("pending_intent"))
        return stalled, ledger

    def _recover(self, run_id: str, *extra: str) -> tuple[int, dict, str, BaseException | None]:
        out, err = io.StringIO(), io.StringIO()
        escaped: BaseException | None = None
        code = -1
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = launcher.run_watchdog_cli(
                    ["recover", "--run-id", run_id, "--artifact-base", str(self.base),
                     "--adapter", "standalone", "--json", *extra])
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

    def test_a_stalled_run_is_recovered_and_its_pending_intent_really_executes(self) -> None:
        stalled, ledger = self._stall("run_f5stall")
        pending = stalled["pending_intent"]["intent_id"]
        self.assertTrue(launcher.standalone_profile_path(self.base, "run_f5stall").is_file(),
                        "the launcher persisted no profile for the watchdog to rebuild from")
        head_before = recovery_runtime.resolve_head(
            "run_f5stall", artifact_base=self.base).head_checkpoint_id
        code, summary, stderr, escaped = self._recover("run_f5stall")
        self.assertIsNone(escaped, f"the recovery escaped as {escaped!r}")
        self.assertEqual(code, 0, f"{summary!r}\n{stderr}")
        self.assertEqual(summary.get("status"), "RECOVERED", summary)
        self.assertTrue(summary.get("effect_performed"))
        # -- the subsequent execution node really ran: journal, ledger, route ------------
        journal = journal_mod.ExecutionJournal(self.base, "run_f5stall")
        settled = [row for row in journal.rows_for(pending)
                   if row["kind"] == "SETTLEMENT_OBSERVED"]
        self.assertEqual(len(settled), 1, "the pending intent never settled")
        self.assertEqual(settled[0]["state"], "COMPLETED")
        self.assertEqual(settled[0]["axes"]["process_liveness"], "already exited")
        self.assertIsNotNone(ledger.get_settlement(pending),
                             "the recovery settled into a ledger that is not the run's own")
        self.assertEqual(ledger.get_receipt(pending)["status"], "SETTLED")
        head_after = recovery_runtime.resolve_head(
            "run_f5stall", artifact_base=self.base)
        self.assertNotEqual(head_after.head_checkpoint_id, head_before)
        self.assertEqual(head_after.state.get("terminal_status"), "COMPLETED",
                         head_after.state.get("terminal_reason"))
        self.assertFalse(pid_alive(int(settled[0]["source_vocabulary"]["pid"])))

    def test_a_run_with_no_persisted_profile_is_refused_by_name_not_by_traceback(self) -> None:
        self._stall("run_f5nopro")
        launcher.standalone_profile_path(self.base, "run_f5nopro").unlink()
        code, summary, stderr, escaped = self._recover("run_f5nopro")
        self.assertIsNone(escaped, f"the recovery escaped as {escaped!r}")
        self.assertEqual(code, 1)
        self.assertEqual(summary.get("code"), ports.RECOVERY_GRAPH_UNAVAILABLE,
                         summary)
        self.assertIn(launcher.STANDALONE_ADAPTER_REQUIRES_PROFILE,
                      str(summary.get("detail")))
        # And an operator-supplied profile recovers it.
        profile_file = self.base / "operator-profile.json"
        profile_file.write_text(json.dumps(
            agent_profile_spec(worktree=str(self.base / "worktree"))))
        code, summary, stderr, escaped = self._recover(
            "run_f5nopro", "--standalone-profile", str(profile_file))
        self.assertIsNone(escaped)
        self.assertEqual(summary.get("status"), "RECOVERED", f"{summary!r}\n{stderr}")

    def test_the_capability_authority_still_touches_no_process(self) -> None:
        source = inspect.getsource(launcher._watchdog_wiring)
        capability_branch = source.split("def capabilities_for", 1)[1].split("from . import turn_boundary", 1)[0]
        self.assertIn("StandaloneAdapter(\n                None", capability_branch,
                      "the capability question must still be asked of a runtime-less "
                      "adapter, or the run looks alive to the gate deciding it is stalled")


# =====================================================================================
# F6 -- every runtime failure is a typed outcome at the executor boundary
# =====================================================================================
class F06RuntimeFailuresAreTypedOutcomesTests(_Composed):

    def _adapter(self, run_id: str):
        spec = stub_profile_spec("ready", worktree=self.worktree)
        return self.compose_spec(spec, run_id=run_id)

    def test_each_named_runtime_failure_settles_as_a_typed_failure(self) -> None:
        cases = [
            (runtime_mod.drivers.IdentityBindingUnverified("second id"),
             "identity_binding_violated"),
            (runtime_mod.drivers.DeliveryModeMismatch("no intent"), "delivery_mode_mismatch"),
            (identity.StandaloneTeardownUnproven("unproven"), "teardown_unproven"),
            (identity.OwnershipRefused("refused"), "ownership_refused"),
            (pty_supervisor.ProcessTableUnreadable("ps failed"), "process_table_unreadable"),
            (OSError(errno.EIO, "pty"), "os_error"),
        ]
        for number, (exc, stage) in enumerate(cases):
            with self.subTest(exception=type(exc).__name__):
                run_id = f"run_f6x{number}"
                adapter, _state, ledger = self._adapter(run_id)
                intent = self.intent(f"intent-f6-{number}", run_id=run_id)
                session = adapter.runtime.session_for(intent)

                def raising(_exc=exc, **_kwargs):
                    raise _exc
                session.run_dispatch = raising
                claim = ledger.claim(intent)
                receipt = adapter.start(intent, lease_token=claim["lease_token"])
                self.assertEqual(receipt["outcome"], "failed")
                self.assertEqual(receipt["failure_stage"], stage)
                rows = self.settlement_rows(run_id, intent["intent_id"])
                self.assertEqual(len(rows), 1, f"{stage}: no typed settlement journalled")
                self.assertEqual(rows[0]["outcome"], "failed")
                self.assertEqual(rows[0]["source_vocabulary"]["completion_verdict"]["stage"],
                                 stage)
                event = ledger.get_settlement(intent["intent_id"])
                self.assertIsNotNone(event)
                self.assertEqual(event["result"]["status"], "BLOCKED")
                self.assertEqual(event["result"]["standalone_failure"]["stage"], stage)
                self.assertEqual(adapter.settlement(intent["intent_id"])["event_id"],
                                 event["event_id"])

    def test_a_programming_error_still_propagates(self) -> None:
        adapter, _state, ledger = self._adapter("run_f6prog")
        intent = self.intent("intent-f6-prog", run_id="run_f6prog")
        session = adapter.runtime.session_for(intent)

        def raising(**_kwargs):
            raise TypeError("a bug, not a dispatch outcome")
        session.run_dispatch = raising
        claim = ledger.claim(intent)
        with self.assertRaises(TypeError):
            adapter.start(intent, lease_token=claim["lease_token"])
        self.assertIsNone(ledger.get_settlement("intent-f6-prog"))

    def test_the_failure_table_is_closed_and_names_the_review_s_three(self) -> None:
        for exc in (runtime_mod.drivers.IdentityBindingUnverified("x"),
                    runtime_mod.drivers.DeliveryModeMismatch("x"),
                    identity.StandaloneTeardownUnproven("x")):
            self.assertIsNotNone(runtime_mod.failure_stage_for(exc), type(exc).__name__)
        self.assertIsNone(runtime_mod.failure_stage_for(TypeError("x")))
        self.assertIsNone(runtime_mod.failure_stage_for(KeyError("x")))


# =====================================================================================
# F7 -- a refused admission never reaches the ledger
# =====================================================================================
class F07SettleFailedHonoursAdmissionRefusalTests(_Composed):

    def test_a_foreign_incarnation_refusal_leaves_the_ledger_unsettled(self) -> None:
        spec = stub_profile_spec("ready", worktree=self.worktree)
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f7")
        intent = self.intent("intent-f7", run_id="run_f7")
        claim = ledger.claim(intent)
        ledger.record_receipt("intent-f7", {"intent_id": "intent-f7", "task_id": "t",
                                            "dispatch_id": "d-old",
                                            "external_id": "s-old:i-old"},
                              claim["lease_token"])
        session = adapter.runtime.session_for(intent)
        session._journal(kind="EVENT", derived_from="pty", event="spawned",
                         state="STARTING", vocabulary={"note": "an open dispatch"})
        failure = runtime_mod.StandaloneDispatchFailed("readiness_timed_out", "deadline")
        with self.assertRaises(runtime_mod.StandaloneDispatchUnsettled) as refused:
            session.settle_failed(failure, lease_token=claim["lease_token"])
        self.assertEqual(refused.exception.cause, "settlement_refused")
        self.assertEqual(refused.exception.evidence["code"], journal_mod.FOREIGN_INCARNATION)
        self.assertIsNone(ledger.get_settlement("intent-f7"),
                          "the ledger was settled although the journal refused the row")
        self.assertEqual(self.settlement_rows("run_f7", "intent-f7"), [])
        self.assertIn("intent-f7", adapter.open_dispatches())
        self.assertIsNone(adapter.settlement("intent-f7"))

    def test_the_executor_boundary_projects_the_refusal_onto_a_typed_terminal(self) -> None:
        spec = stub_profile_spec("ready", worktree=self.worktree)
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f7b")
        intent = self.intent("intent-f7b", run_id="run_f7b")
        claim = ledger.claim(intent)
        ledger.record_receipt("intent-f7b", {"intent_id": "intent-f7b", "task_id": "t",
                                             "dispatch_id": "d-old",
                                             "external_id": "s-old:i-old"},
                              claim["lease_token"])
        session = adapter.runtime.session_for(intent)

        def raising(**_kwargs):
            raise runtime_mod.StandaloneDispatchFailed("readiness_timed_out", "deadline")
        session.run_dispatch = raising
        with self.assertRaises(executor.IdempotencyRecoveryError) as blocked:
            adapter.start(intent, lease_token=claim["lease_token"])
        self.assertEqual(blocked.exception.code, "IDEMPOTENCY_RECOVERY_BLOCKED")
        self.assertIn("settlement_refused", blocked.exception.detail)
        self.assertIsNone(ledger.get_settlement("intent-f7b"))


# =====================================================================================
# F8 -- recover_handle verifies against a live authority
# =====================================================================================
class F08RecoverHandleIsNotTautologicalTests(_Composed):

    def test_a_live_session_is_verified_and_a_lost_one_is_detected(self) -> None:
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree,
                                 driver_env={"OS37_STUB_MODE": "alive", "OS37_STUB_AUTH": "ok"})
        adapter, _state, ledger = self.compose(profile, run_id="run_f8")
        intent = self.intent("intent-f8", run_id="run_f8")
        claim = ledger.claim(intent)
        spawned = adapter.spawn_only(
            intent, lease_token=claim["lease_token"], payload="work",
            rehearsal=lambda p, e, s: {"channel": "structured", "record_type": "system",
                                       "session_id": s},
            mode_rehearsal=lambda p, e: {"r_b_closed": True, "delivery_proof": True,
                                         "auth_marker": None, "waited_without_prompt": False,
                                         "evaluable": True, "identity_bound": True,
                                         "detail": {}})
        self.assertEqual(spawned["start_outcome"], "ready", spawned)
        session = adapter.runtime.session("intent-f8")
        live = adapter.recover_handle("intent-f8")
        self.assertEqual(live["handle_recovery"], "listing_verified", live)
        self.assertEqual(live["handle"], session.pty["pty_id"])
        # A supervisor crash that takes the pty session with it, with NO sentinel: the
        # agent and its watcher are killed outright, so nothing writes an exit.
        for target in (int(session.pty["leader_pid"]), int(session.record["pid"])):
            with contextlib.suppress(OSError):
                os.kill(target, signal.SIGKILL)
        os.waitpid(int(session.pty["leader_pid"]), 0)
        sentinel = pty_supervisor.exit_sentinel_path(self.base, "run_f8", session.session_id,
                                                     session.incarnation)
        with contextlib.suppress(FileNotFoundError):
            sentinel.unlink()
        deadline = time.time() + 5
        while pid_alive(int(session.record["pid"])) and time.time() < deadline:
            time.sleep(0.05)
        lost = adapter.recover_handle("intent-f8")
        self.assertEqual(lost["handle_recovery"], "not_listed", lost)
        self.assertIsNone(lost["handle"])
        with self.assertRaises(pause_policy.PauseRefused) as refused:
            pause_policy.refuse_unrecovered_handle({"intent_id": "intent-f8"},
                                                   lost["handle_recovery"])
        self.assertEqual(refused.exception.code, "TERMINAL_ORPHAN_POSSIBLE")

    def test_a_settled_session_is_verified_by_its_fenced_exit_sentinel(self) -> None:
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        adapter, _state, ledger = self.compose(profile, run_id="run_f8s")
        intent = self.intent("intent-f8s", run_id="run_f8s")
        self.dispatch(adapter, ledger, intent)
        verified = adapter.recover_handle("intent-f8s")
        self.assertEqual(verified["handle_recovery"], "listing_verified", verified)
        self.assertEqual(verified["exit_status"], 0)
        stranger = StandaloneAdapter(None, runtime_state=ledger,
                                     settlement_journal=self.journal("run_f8s"),
                                     artifact_base=self.base, run_id="run_f8s")
        self.assertEqual(stranger.recover_handle("intent-f8s")["handle_recovery"],
                         "listing_verified")


# =====================================================================================
# F9 -- successful completion reclaims the pty and the exit watcher
# =====================================================================================
class F09CompletionReclaimsResourcesTests(_Composed):

    def test_repeated_completed_dispatches_leak_no_fd_and_no_zombie(self) -> None:
        spec = agent_profile_spec(worktree=self.worktree)
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f9")
        before = open_fds()
        for number in range(3):
            intent = self.intent(f"intent-f9-{number}", run_id="run_f9")
            receipt, _event = self.dispatch(adapter, ledger, intent)
            self.assertEqual(receipt["outcome"], "succeeded", receipt)
        self.assertEqual(open_fds() - before, set(), "master descriptors leaked")
        leaders = [int(s.pty["leader_pid"]) for s in adapter.runtime.sessions.values()]
        self.assertEqual(zombies_among(*leaders), [], "exit watchers were left as zombies")
        for session in adapter.runtime.sessions.values():
            self.assertEqual(session.pty["master_fd"], -1)
            with self.assertRaises(ChildProcessError):
                os.waitpid(int(session.pty["leader_pid"]), os.WNOHANG)
            reclaim = [row for row in self.journal("run_f9").rows_for(session.intent_id)
                       if row["source_vocabulary"].get("master_fd_closed")]
            self.assertEqual(len(reclaim), 1)
            self.assertTrue(reclaim[0]["source_vocabulary"]["leader_reaped"])
            self.assertEqual(reclaim[0]["axes"]["process_liveness"], "already exited")

    def test_the_exit_watcher_holds_no_pty_descriptor_while_it_waits(self) -> None:
        """The watcher holds no SLAVE descriptor and its stdio is /dev/null, so the slave's
        last close is the agent's own.  Round 4 (finding 1) corrected the round-3 half of
        this that closed the MASTER too: with the supervisor holding the only master its
        death hung the pty up and destroyed the agent and the exit evidence.  The watcher
        keeps exactly one master copy as a keepalive it never reads while the supervisor
        lives, and the no-leak assertion above still holds because the watcher exits --
        and drops it -- the moment the agent is reaped.  The real-subprocess proof is
        `test_os37_lifecycle_boundary_regressions.F01SupervisorDeathTests`."""
        source = inspect.getsource(pty_supervisor._watch)
        self.assertIn("os.close(slave_fd)", source)
        self.assertIn("os.devnull", source)
        self.assertIn("keep = {master_fd, guard_r}", source)
        self.assertNotIn("os.close(master_fd)", source)
        self.assertIn("signal.signal(signal.SIGHUP, signal.SIG_IGN)", source)


# =====================================================================================
# F10 -- exit 0 without a completion record is never a success
# =====================================================================================
class F10ExitZeroWithoutRecordTests(_Composed):

    def test_exit_0_with_no_record_settles_failed_consistently(self) -> None:
        spec = agent_profile_spec(
            worktree=self.worktree,
            driver_env={"OS37_GA_NO_RESULT_ONCE": str(self.base / "once")},
            exit_code_map={"0": "COMPLETED"})
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f10")
        intent = self.intent("intent-f10", run_id="run_f10")
        receipt, event = self.dispatch(adapter, ledger, intent)
        self.assertEqual(receipt["outcome"], "failed",
                         "a process that exited 0 without declaring a result was reported "
                         "as a success")
        rows = self.settlement_rows("run_f10", "intent-f10")
        self.assertEqual([(r["state"], r["outcome"]) for r in rows], [("FAILED", "failed")])
        self.assertEqual(rows[0]["source_vocabulary"]["exit_status"], 0)
        self.assertEqual(event["result"]["status"], "BLOCKED")
        self.assertEqual(event["result"]["standalone_failure"]["reason"],
                         "no_completion_record")
        self.assertEqual(adapter.settlement("intent-f10")["event_id"], event["event_id"])

    def test_await_completion_never_answers_completed_without_a_record(self) -> None:
        mapped = lifecycle.map_exit_code(0, {0: "COMPLETED"})
        self.assertEqual(mapped["state"], "COMPLETED")
        source = inspect.getsource(runtime_mod.StandaloneSession.await_completion)
        self.assertIn('"FAILED" if mapped["state"] in lifecycle.SETTLED_STATES', source)


# =====================================================================================
# F11 / F12 / F16 -- the interrupt ladder
# =====================================================================================
RECORD = {"pid": 4242, "pgid": 4242, "sid": 4241, "captured_tty": "ttys042",
          "run_id": "r", "repo_id": "x", "worktree_selector": "w", "agent_id": "a",
          "task_id": "t", "dispatch_id": "d", "session_id": "s", "pty_id": "p",
          "process_incarnation": "i", "host_scope": "local", "spawn_token": "tok",
          "started_at": "", "argv_digest": "", "env_digest": "",
          "created_by_this_runtime": True, "resource_kind": "pty_session",
          "user_taken_over": False}
AGENT_ROW = {"pid": 4242, "ppid": 4241, "pgid": 4242, "sid": 4241, "tty": "ttys042",
             "stat": "S+"}
LEADER_ROW = {"pid": 4241, "ppid": 1, "pgid": 4241, "sid": 4241, "tty": "ttys042",
              "stat": "Ss"}


def _ladder_profile():
    return profile_from_mapping(stub_profile_spec(
        "ready", worktree="/", timeouts={"graceful_force_timeout_ms": 200,
                                         "force_retry_ms": 20,
                                         "physical_exit_timeout_ms": 200,
                                         "staleness_budget_ms": 1000}))


def _table(rows_by_call, *, stale_after: int | None = None):
    calls = {"n": 0}

    def reader(tty: str) -> dict:
        calls["n"] += 1
        rows = rows_by_call(calls["n"])
        age = 100.0 if stale_after is not None and calls["n"] > stale_after else 0.0
        return {"tty": tty, "captured_at": time.time() - age, "readable": rows is not None,
                "rows": tuple(rows or ())}
    return reader


class F11DeliveredSignalIsNeverNotOwnedTests(unittest.TestCase):

    def test_a_refusal_after_rung_1_delivered_is_exit_unproven(self) -> None:
        signals: list = []
        result = interrupt_mod.interrupt(
            "i", "stop", record=RECORD, profile=_ladder_profile(),
            table_reader=_table(lambda n: (AGENT_ROW, LEADER_ROW), stale_after=1),
            supervisor_pid=1, killpg=lambda pg, sig: signals.append((pg, sig)),
            kill=lambda p, sig: signals.append((p, sig)))
        self.assertTrue(signals, "rung 1 delivered nothing; the case is vacuous")
        self.assertEqual(result["interrupt_outcome"], "exit_unproven")
        self.assertEqual(interrupt_mod.lifecycle_for("exit_unproven"),
                         {"state": "LOST", "lost_reason": "stop_unverified"})

    def test_g2_refusal_after_a_delivered_signal_is_exit_unproven(self) -> None:
        signals: list = []
        # A 1 ms graceful window: rung 2's loop never runs, so the SECOND table read is
        # G2's -- and it is UNREADABLE.  Rung 1 has already delivered.
        profile = profile_from_mapping(stub_profile_spec(
            "ready", worktree="/", timeouts={"graceful_force_timeout_ms": 1,
                                             "force_retry_ms": 1,
                                             "physical_exit_timeout_ms": 50,
                                             "staleness_budget_ms": 1000}))
        ticks = [0.0]

        def clock() -> float:
            ticks[0] += 1.0          # every reading is a second later: no wait loop runs
            return ticks[0]
        result = interrupt_mod.interrupt(
            "i", "stop", record=RECORD, profile=profile,
            table_reader=_table(lambda n: (AGENT_ROW, LEADER_ROW) if n <= 1 else None),
            supervisor_pid=1, killpg=lambda pg, sig: signals.append((pg, sig)),
            kill=lambda p, sig: signals.append((p, sig)), sleep=lambda s: None,
            clock=clock)
        self.assertTrue(signals, "rung 1 delivered nothing; the case is vacuous")
        self.assertEqual(result["ladder"][-1]["rung"], "G2", result["ladder"])
        self.assertEqual(result["interrupt_outcome"], "exit_unproven")

    def test_no_signal_delivered_is_still_not_owned(self) -> None:
        signals: list = []
        result = interrupt_mod.interrupt(
            "i", "stop", record=RECORD, profile=_ladder_profile(),
            table_reader=_table(lambda n: (AGENT_ROW, LEADER_ROW), stale_after=0),
            supervisor_pid=1, killpg=lambda pg, sig: signals.append((pg, sig)),
            kill=lambda p, sig: signals.append((p, sig)))
        self.assertEqual(signals, [])
        self.assertEqual(result["interrupt_outcome"], "not_owned")


class F12ExitWatcherIsNeverSignalledTests(unittest.TestCase):

    def test_group_scope_signals_descendants_and_withholds_the_watcher(self) -> None:
        snapshot = {"tty": "ttys042", "captured_at": time.time(), "readable": True,
                    "rows": (LEADER_ROW, AGENT_ROW)}
        permit = identity.assert_may_act(RECORD, "signal", observed=AGENT_ROW)
        decision = pty_supervisor.check_ownership(RECORD, snapshot, staleness_budget_ms=1000,
                                                  supervisor_pid=1)
        self.assertEqual(decision["scope"], "group")
        sent: list = []
        outcome = pty_supervisor.signal_target(RECORD, decision, 15, permit=permit,
                                               snapshot=snapshot,
                                               killpg=lambda pg, sig: sent.append(pg))
        self.assertEqual(sent, [4242], "the exit watcher's group was signalled")
        withheld = [step for step in outcome["sent"] if step["rung"] == "session_leader"]
        self.assertEqual(withheld[0]["result"], "withheld:exit_watcher")

    def test_a_real_interrupt_leaves_the_watcher_alive_to_write_the_sentinel(self) -> None:
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, True)
        prof = profile_from_mapping(stub_profile_spec(
            "alive", worktree=str(base), timeouts={"graceful_force_timeout_ms": 1000,
                                                   "physical_exit_timeout_ms": 3000}))
        env = {"PATH": f"{_stub_dir()}:/usr/bin:/bin", "OS37_STUB_MODE": "alive",
               "HOME": os.environ.get("HOME", "/")}
        target = pty_supervisor.spawn_record_path(base, "run_f12", "intent-f12", "i-1")
        sentinel = base / "exit.i-1"
        session = pty_supervisor.spawn(
            argv=("os37-stub-cli", "--session-id", "s-1"), env=env, profile=prof,
            session_id="s-1", incarnation="i-1", spawn_record_target=str(target),
            cwd=str(base), sentinel=str(sentinel), fence="s-1:i-1")
        self.addCleanup(kill_and_reap, session["pid"], session["leader_pid"])
        self.addCleanup(pty_supervisor.release, session)
        record = identity.make_record(
            run_id="run_f12", repo_id="x",
            worktree_selector=identity.stable_worktree_selector("x", str(base)),
            agent_id="a",
            task_id="t", dispatch_id="d", session_id="s-1", pid=session["pid"],
            pgid=session["pgid"], sid=session["sid"],
            captured_tty=runtime_mod._tty_name(session["slave_name"]),
            pty_id=session["pty_id"], process_incarnation="i-1", host_scope="local",
            spawn_token="tok", started_at="1970-01-01T00:00:00Z", argv_digest="d",
            env_digest="e", created_by_this_runtime=True, resource_kind="pty_session",
            user_taken_over=False)
        result = interrupt_mod.interrupt(
            "intent-f12", "stop", record=record, profile=prof,
            drain=lambda: pty_supervisor.drain(session["master_fd"], budget_ms=50))
        self.assertIn(result["interrupt_outcome"],
                      ("interrupted_confirmed", "terminated_forced"), result)
        deadline = time.time() + 5
        while not sentinel.exists() and time.time() < deadline:
            time.sleep(0.05)
        read = pty_supervisor.read_exit_sentinel(sentinel, fence="s-1:i-1")
        self.assertEqual(read["outcome"], "exited",
                         "the watcher did not survive to write the exit sentinel; "
                         "proof-of-death was defeated by the ladder itself")
        self.assertIn(read["code"], (128 + signal.SIGTERM, 128 + signal.SIGKILL))
        reaped = pty_supervisor.reap_leader(session, timeout_ms=3000)
        self.assertTrue(reaped["reaped"], reaped)


class F16AbsentIsNotUnreadableTests(unittest.TestCase):

    def test_observed_row_distinguishes_absent_from_unreadable(self) -> None:
        readable_absent = {"readable": True, "rows": (), "captured_at": time.time(),
                           "tty": "ttys042"}
        self.assertEqual(interrupt_mod.observed_row(readable_absent, 4242), {})
        self.assertIsNone(interrupt_mod.observed_row({"readable": False, "rows": ()}, 4242))
        verdict = identity.verify(RECORD, {})
        self.assertEqual((verdict["verdict"], verdict["reason"]),
                         ("not_owned", "pid_absent_from_table"))

    def test_an_already_exited_process_reaches_proof_of_exit(self) -> None:
        signals: list = []
        result = interrupt_mod.interrupt(
            "i", "stop", record=RECORD, profile=_ladder_profile(),
            table_reader=_table(lambda n: ()), supervisor_pid=1,
            killpg=lambda pg, sig: signals.append(pg), kill=lambda p, sig: signals.append(p))
        self.assertEqual(signals, [])
        self.assertEqual(result["interrupt_outcome"], "interrupted_confirmed", result)
        self.assertEqual(result["ladder"][-1]["rung"], "rung_4_proof_of_death")
        self.assertIn("already exited", result["ladder"][-1]["detail"])

    def test_release_terminal_names_an_absent_pid_as_absent(self) -> None:
        with self.assertRaises(identity.OwnershipRefused) as refused:
            interrupt_mod.release_terminal(RECORD, authority="authorized",
                                           worker_resource="release", observed={})
        self.assertIn("pid_absent_from_table", str(refused.exception))
        self.assertNotIn("process_table_unreadable", str(refused.exception))


class F16AdapterReleaseOverAnExitedProcessTests(_Composed):

    def test_the_adapter_release_verb_reports_an_exited_process_as_absent(self) -> None:
        """Through `StandaloneAdapter.release_terminal`: the process has provably exited
        and the table is readable, so the refusal is `not_owned` (pid absent) -- never
        `unverifiable` (table unreadable), which is what the caller used to hand up."""
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        adapter, _state, ledger = self.compose(profile, run_id="run_f16")
        intent = self.intent("intent-f16", run_id="run_f16")
        self.dispatch(adapter, ledger, intent)
        session = adapter.runtime.session("intent-f16")
        self.assertFalse(pid_alive(int(session.record["pid"])))
        snapshot = session._snapshot()
        self.assertTrue(snapshot["readable"], "the real process table must be readable here")
        outcome = adapter.release_terminal("intent-f16", authority="authorized")
        self.assertEqual(outcome["refusal"], "not_owned", outcome)
        self.assertEqual(outcome["process_liveness"], "already exited")


# =====================================================================================
# F13 -- a dropped byte makes the store truncated, once
# =====================================================================================
class F13CaptureTruncationTests(unittest.TestCase):

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)

    def test_an_oversized_chunk_makes_the_capture_unanswerable(self) -> None:
        cap = capture_mod.BoundedCapture(
            self.base / "cap.log",
            limits=CaptureLimits(max_line_bytes=16, max_total_bytes=64, max_records=10))
        record = cap.append(b'{"type":"result","is_error":false,"result":"LOST"}\n', at="t")
        self.assertEqual(record["truncation"], capture_mod.TRUNCATION_LINE_BYTES)
        self.assertTrue(cap.truncated)
        self.assertEqual(cap.truncation, capture_mod.TRUNCATION_LINE_BYTES)
        self.assertEqual(cap.dropped_bytes, len(b'{"type":"result","is_error":false,"result":"LOST"}\n') - 16)
        answer = cap.completion_is_answerable()
        self.assertFalse(answer["answerable"])
        self.assertEqual(answer["lost_reason"], capture_mod.CAPTURE_TRUNCATED_LOST_REASON)
        # Durable: a stranger reading the same store sees the same verdict.
        again = capture_mod.BoundedCapture(self.base / "cap.log")
        self.assertTrue(again.truncated)

    def test_dropped_bytes_are_counted_exactly_once(self) -> None:
        cap = capture_mod.BoundedCapture(
            self.base / "cap2.log",
            limits=CaptureLimits(max_line_bytes=16, max_total_bytes=20, max_records=10))
        cap.append(b"x" * 20, at="t")           # cut to 16: 4 dropped
        cap.append(b"y" * 30, at="t")           # total limit: the whole 30 dropped
        self.assertEqual(cap.dropped_bytes, 34)
        self.assertEqual(cap.size, 16)

    def test_a_whole_chunk_leaves_the_store_answerable(self) -> None:
        cap = capture_mod.BoundedCapture(self.base / "cap3.log", limits=CaptureLimits())
        cap.append(b'{"type":"result"}\n', at="t")
        self.assertFalse(cap.truncated)
        self.assertTrue(cap.completion_is_answerable()["answerable"])


# =====================================================================================
# F14 -- the composed argv reaches the replay selector
# =====================================================================================
class F14DeliveryVerificationSeesTheRealArgvTests(_Composed):

    def test_the_pty_session_carries_the_execd_argv(self) -> None:
        self.assertIn("argv", pty_supervisor.PtySession.__annotations__)
        # `launch_with_prompt` over the stub's `agent` mode: it emits readiness and then
        # waits on stdin, so the runtime reaches `await_delivery` -- the one place the
        # composed argv is handed to the driver's replay selector -- with the process
        # alive.  No delivery proof arrives, so the dispatch ends as a NAMED delivery
        # failure (and, since finding 1, a terminated and reaped one).
        from scripts.deterministic_workflow.standalone_profile import Timeouts
        profile = replay_profile(
            stream=STREAMS / "m14_claude_genuine_turn.stream", exit_code=0,
            worktree=self.worktree,
            driver_env={"OS37_STUB_MODE": "agent", "OS37_STUB_AUTH": "ok"},
            identity_binding="minted_echo", identity_flag="--session-id",
            timeouts=Timeouts(preflight_timeout_ms=3_000, readiness_timeout_ms=10_000,
                              delivery_verify_timeout_ms=1_500,
                              completion_timeout_ms=3_000,
                              graceful_force_timeout_ms=1_000))
        spec = profile_spec(profile)
        spec["identity_flag"] = "--session-id"
        adapter, _state, ledger = self.compose_spec(spec, run_id="run_f14")
        intent = self.intent("intent-f14", run_id="run_f14")
        session = adapter.runtime.session_for(intent)
        seen: list = []
        real = session.driver.delivery_evidence

        def spy(text, **kwargs):
            seen.append(tuple(kwargs.get("composed_argv", ())))
            return real(text, **kwargs)
        session.driver.delivery_evidence = spy
        session.start = _with_injected_rehearsals(session.start)
        receipt, _event = self.dispatch(adapter, ledger, intent)
        self.assertEqual(receipt["outcome"], "failed", receipt)
        self.assertEqual(receipt["failure_stage"], "delivery_not_observed", receipt)
        self.assertTrue(seen, "delivery verification never consulted the driver")
        self.assertTrue(all(argv for argv in seen),
                        f"the replay selector received an EMPTY argv: {seen[:2]!r}")
        self.assertEqual(seen[0], tuple(session.pty["argv"]))
        self.assertIn("os37-stub-cli", seen[0][0])
        self.assertIn(intent["intent_id"], " ".join(seen[0]),
                      "the composed argv does not carry the dispatched prompt")


# =====================================================================================
# F15 -- the identity bind reads its own incarnation's record
# =====================================================================================
class F15SpawnRecordIsIncarnationScopedTests(unittest.TestCase):

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)
        self.dir = pty_supervisor.spawn_record_dir(self.base, "run_f15", "intent-f15")
        self.dir.mkdir(parents=True)
        (self.dir / "spawn.i-aaaa").write_text(json.dumps({"process_incarnation": "i-aaaa",
                                                            "pid": 1}))

    def test_read_spawn_records_is_scoped_to_the_incarnation_asked_about(self) -> None:
        (self.dir / "spawn.i-zzzz").write_text(json.dumps({"process_incarnation": "i-zzzz",
                                                            "pid": 2}))
        own = pty_supervisor.read_spawn_records(self.base, "run_f15", "intent-f15",
                                                incarnation="i-aaaa")
        self.assertEqual(own["record"]["process_incarnation"], "i-aaaa")
        absent = pty_supervisor.read_spawn_records(self.base, "run_f15", "intent-f15",
                                                   incarnation="i-new")
        self.assertEqual(absent["outcome"], "absent")
        # The UNSCOPED question `lookup` asks is unchanged: any execve for this intent.
        self.assertEqual(pty_supervisor.read_spawn_records(
            self.base, "run_f15", "intent-f15")["outcome"], "present")
        # A file named for one incarnation whose body names another is not evidence.
        (self.dir / "spawn.i-liar").write_text(json.dumps({"process_incarnation": "i-other",
                                                            "pid": 3}))
        self.assertEqual(pty_supervisor.read_spawn_records(
            self.base, "run_f15", "intent-f15", incarnation="i-liar")["outcome"], "unknown")

    def test_a_new_attempt_does_not_bind_to_an_earlier_incarnations_record(self) -> None:
        """The child of the new attempt never reaches execve (a spawner that writes no
        record); the earlier record on disk must not bind it."""
        prof = profile_from_mapping(stub_profile_spec("ready", worktree=str(self.base)))
        intent = {"intent_id": "intent-f15", "command_id": "c", "payload_digest": "d",
                  "run_id": "run_f15", "phase": "IMPLEMENTATION", "role": "WORKER",
                  "round_kind": "PHASE_GATE", "action_kind": "DISPATCH_AGENT"}
        journal = journal_mod.ExecutionJournal(self.base, "run_f15")
        ledger = InMemoryRuntimeStateStore()
        claim = ledger.claim(intent)
        spawned = {}

        def spawner(**kwargs):
            # A real pty so teardown can be proven, but the child is a plain `sleep`
            # that writes NO spawn record -- the shape of an attempt that died pre-exec.
            session = pty_supervisor.spawn(
                argv=("/bin/sleep", "30"), env={"PATH": "/usr/bin:/bin"},
                profile=prof, session_id=kwargs["session_id"],
                incarnation=kwargs["incarnation"],
                spawn_record_target=str(self.base / "never-written"), cwd=str(self.base),
                sentinel=kwargs["sentinel"], fence=kwargs["fence"], image="/bin/sleep")
            spawned.update(session)
            return session
        session = runtime_mod.StandaloneSession(
            intent=intent, profile=prof, artifact_base=self.base, run_id="run_f15",
            journal=journal, runtime_state=ledger, spawner=spawner)
        try:
            receipt = session.start(
                lease_token=claim["lease_token"],
                help_text="--bare --settings --session-id -p --output-format stream-json",
                prober=lambda argv, env, **_k: {"outcome": "completed",
                                                "output": "os37-stub-cli 1.2.3",
                                                "exit_code": 0},
                rehearsal=lambda p, e, s: {"channel": "structured", "record_type": "system",
                                           "session_id": s},
                mode_rehearsal=lambda p, e: {"r_b_closed": True, "delivery_proof": True,
                                             "auth_marker": None,
                                             "waited_without_prompt": True,
                                             "evaluable": True, "identity_bound": True,
                                             "detail": {}})
        finally:
            kill_and_reap(*(int(t) for t in (spawned.get("pid"), spawned.get("leader_pid")) if t))
        self.assertNotEqual(receipt["start_outcome"], "ready",
                            "the new attempt bound to an EARLIER incarnation's spawn record")
        self.assertEqual(receipt["failure_reason"], "no_execve")
        self.assertIsNone(ledger.get_receipt("intent-f15").get("receipt"))
        self.assertEqual([row["kind"] for row in journal.rows_for("intent-f15")
                          if row["kind"] == "SPAWN_OBSERVED"], [])


# =====================================================================================
# F17 -- a torn tail is not tampering
# =====================================================================================
class F17TornTailTests(unittest.TestCase):

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, True)
        self.journal = journal_mod.ExecutionJournal(self.base, "run_f17")
        self.journal.append(journal_mod.make_record(
            kind="EVENT", derived_from="pty", intent_id="i1", event="spawned",
            state="STARTING"))

    def _tear(self) -> None:
        with open(self.journal.path, "ab") as handle:
            handle.write(b'{"seq": 2, "kind": "EVENT", "intent_id": "i1", "torn')

    def test_a_torn_trailing_line_keeps_the_journal_readable(self) -> None:
        self._tear()
        rows = self.journal.rows()
        self.assertEqual([row["seq"] for row in rows], [1])
        self.assertEqual(self.journal.torn_tail_bytes, len(b'{"seq": 2, "kind": "EVENT", "intent_id": "i1", "torn'))
        self.assertEqual(self.journal.open_dispatches(), ("i1",))
        snapshot = journal_mod.rediscover("run_f17", self.base)
        self.assertIn("i1", snapshot["intents"])

    def test_an_append_after_a_torn_tail_produces_a_well_formed_journal(self) -> None:
        self._tear()
        written = self.journal.append(journal_mod.make_record(
            kind="EVENT", derived_from="pty", intent_id="i1", event="readiness_observed",
            state="READY"))
        self.assertEqual(written["seq"], 2)
        rows = self.journal.rows()
        self.assertEqual([row["seq"] for row in rows], [1, 2])
        self.assertEqual(self.journal.torn_tail_bytes, 0)
        self.assertTrue(self.journal.path.read_bytes().endswith(b"\n"))

    def test_a_terminated_corrupt_line_is_still_tampering(self) -> None:
        with open(self.journal.path, "ab") as handle:
            handle.write(b'{"seq": 2, "kind": "EVENT", "corrupt": true}\n')
        with self.assertRaises(journal_mod.JournalUnreadable):
            self.journal.rows()
        path = self.journal.path
        path.write_text(path.read_text().split("\n", 1)[0].replace('"STARTING"', '"READY"')
                        + "\n")
        with self.assertRaises(journal_mod.JournalUnreadable):
            self.journal.rows()

    def test_a_whole_record_missing_only_its_newline_is_kept(self) -> None:
        raw = self.journal.path.read_bytes()
        self.journal.path.write_bytes(raw.rstrip(b"\n"))
        self.assertEqual([row["seq"] for row in self.journal.rows()], [1])
        self.assertEqual(self.journal.torn_tail_bytes, 0)


# =====================================================================================
# F18 -- malformed auth markers are refused
# =====================================================================================
class F18AuthMarkersAreValidatedTests(unittest.TestCase):

    def test_a_malformed_marker_raises_profile_error(self) -> None:
        base = stub_profile_spec("ready", worktree="/")
        for bad in (["error", "authentication_failed", "extra"], "notapair", 42,
                    ["error", 7], ["", "x"]):
            with self.subTest(marker=bad):
                with self.assertRaises(ProfileError):
                    profile_from_mapping({**base, "auth_markers": [bad]})
        with self.assertRaises(ProfileError):
            profile_from_mapping({**base, "auth_markers": "error"})

    def test_a_well_formed_marker_is_kept(self) -> None:
        base = stub_profile_spec("ready", worktree="/")
        profile = profile_from_mapping({**base, "auth_markers": [["error", "authentication_failed"]]})
        self.assertEqual(profile.auth_markers, (("error", "authentication_failed"),))


# =====================================================================================
# F19 -- the tolerated-manifest header counts are derived
# =====================================================================================
class F19ManifestHeaderIsDerivedTests(unittest.TestCase):

    def test_the_checked_in_header_names_the_real_os37_count(self) -> None:
        text = (REPO / "scripts" / "tolerated_skip_manifest.txt").read_text()
        actual = len([line for line in text.splitlines()
                      if line.startswith("always\ttest_os37_")])
        self.assertEqual(actual, 26)
        self.assertIn(f"OS-37 adds {actual} `always` entries", text)
        self.assertNotIn("twenty-one", text)
        self.assertNotIn("{os37_always}", text)

    def test_the_header_is_rendered_from_the_entries(self) -> None:
        alternatives = {
            "test_os37_a.T.test_x": [("always", "r")],
            "test_os37_b.T.test_y": [("always", "r")],
            "test_os37_b.T.test_z": [("always", "r")],
            "test_other.T.test_w": [("always", "r")],
            "test_os37_c.T.test_v": [("not_darwin", "r")],
        }
        header = ci_lane.tolerated_manifest_header(alternatives)
        self.assertIn("OS-37 adds 3 `always` entries", header)
        self.assertIn("1 in test_os37_a, 2 in test_os37_b", header)
        rendered = ci_lane.render_tolerated_manifest(alternatives)
        self.assertTrue(rendered.startswith(header))


if __name__ == "__main__":
    unittest.main()


class F03RelativeArtifactBaseTests(_Composed):
    """Finding 3's ordering moved `chdir` ahead of the spawn record, so the record's path
    must be absolute BEFORE the fork: with a RELATIVE artifact base (what
    `run_workflow.py --artifact-base artifacts` and the R10 harness pass) the child would
    otherwise write it inside the agent's worktree and the parent would bind nothing."""

    def test_a_relative_artifact_base_still_binds_the_spawn_record(self) -> None:
        previous = os.getcwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, previous)
        relative = Path("relative-artifacts")
        spec = agent_profile_spec(worktree=self.worktree)
        ledger = InMemoryRuntimeStateStore()
        adapter, _state = launcher.build_standalone_adapter(
            {"run_id": "run_f3rel", "thread_id": "t", "phases": ["IMPLEMENTATION"]},
            artifact_base=relative, run_id="run_f3rel", runtime_state=ledger,
            profile_spec=spec)
        self.adapters.append(adapter)
        intent = self.intent("intent-f3rel", run_id="run_f3rel")
        receipt, _event = self.dispatch(adapter, ledger, intent)
        self.assertEqual(receipt["outcome"], "succeeded", receipt)
        probe = pty_supervisor.read_spawn_records(relative, "run_f3rel", "intent-f3rel")
        self.assertEqual(probe["outcome"], "present", probe)
        self.assertEqual(
            [p.name for p in Path(self.worktree).rglob("spawn.*")], [],
            "the spawn record was written inside the agent's worktree")
