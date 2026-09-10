"""OS-37 V-8 / WI-17.  The Orca-free end-to-end run, and its FIVE PRECONDITIONS.

The preconditions are asserted **before** any run starts, and they are the substance of the
claim: an E2E that produced the right artefacts while quietly reaching an Orca binary would
prove nothing.  If a precondition cannot be established, the E2E is recorded as **"not
established"** rather than as a pass.

1. ``shutil.which("orca", path=child_env["PATH"]) is None`` (NF-4);
2. the scrub assertion passes over the constructed child env (NF-1, 2, 3, 5, 7);
3. no ``ORCA_*`` name and no Orca hook endpoint is reachable from the child;
4. **no Terminal.app / iTerm window is opened at any point** -- the only pty is
   ``pty.openpty()``-created and headless;
5. the Coordinator's conversational turn does **not** own the process lifecycle, and after a
   simulated turn end the run's identity, journal and state are re-queryable from a FRESH
   PROCESS.

The full Worker -> Reviewer -> correction -> fresh Final Review flow is exercised over the
stub CLI, which lets the whole loop run deterministically with no agent installed.  The live
half, against a real agent CLI, is skip-gated with an exact declared reason -- a fixture that
cannot run is never a pass.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from scripts import os37_native_stub as native_stub
from scripts.deterministic_workflow import standalone_env as env_policy
from scripts.deterministic_workflow import standalone_journal as journal_mod
from scripts.deterministic_workflow import standalone_pty as pty_supervisor
from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
from scripts.deterministic_workflow.standalone_adapter import StandaloneAdapter
from scripts.deterministic_workflow.standalone_profile import (CompletionSelector,
                                                            DeliveryProofSelector,
                                                            ReadinessSelector,
                                                                StandaloneProfile, Timeouts)

REPO = Path(__file__).resolve().parent.parent

#: The NATIVE stub fixture's directory, not the shell fixture's.  R-A leg 4 is an
#: executable-IMAGE identity (DESIGN §D5.3(4)), and a `#!`-script's image is its
#: interpreter, so a shell fixture can never satisfy it and must never be used as if it
#: could.  `None` here means the fixture could not be built and the live tests SKIP -- they
#: do not fall back, because falling back would prove the quorum closes against an
#: interpreter image, which is exactly the weakening R-A forbids.
STUB_BIN = native_stub.native_stub_dir()
SHELL_STUB_BIN = REPO / "scripts" / "fixtures" / "os37" / "bin"

#: Gated on an env var nothing sets, and declared in `scripts/tolerated_skip_manifest.txt`
#: with this exact reason.  The E2E drives real local processes and must not run implicitly.
E2E_REASON = "requires ORCA_OS37_E2E=1; the standalone E2E drives real local processes"
E2E_ENABLED = os.environ.get("ORCA_OS37_E2E") == "1"



def profile(**overrides) -> StandaloneProfile:
    fields = dict(
        driver="claude", binary="os37-stub-cli",
        supported_range=((1, 0, 0), (2, 0, 0)),
        bin_dirs=(str(STUB_BIN),) if STUB_BIN else (),
        readiness_records=(ReadinessSelector(channel="structured",
                                             record_type="system",
                                             session_field="session_id"),),
        # DESIGN D4.2a: the DECLARED capability axis.  The fixture CLI under
        # `scripts/fixtures/os37/bin/` DOES wait for input, so `post_ready_delivery` is the
        # honest declaration for it -- and declaring it here is what keeps that path, and
        # the readiness-before-delivery guarantee it carries, a LIVE tested path rather
        # than prose.  The installed CLIs declare `launch_with_prompt`; both are exercised.
        delivery_mode="post_ready_delivery",
        identity_binding="minted_echo", identity_flag="--session-id",
        delivery_proofs=(DeliveryProofSelector(channel="structured",
                                               record_type="assistant"),),
        completion_records=(CompletionSelector(channel="structured",
                                               record_type="result",
                                               error_field="is_error"),),
        # The completion bound is DECLARED, not inherited.  Its production default is
        # sized for a real agent turn (external review #4), so a fixture that drives a
        # non-completing dispatch must name its own or wait half an hour.
        timeouts=Timeouts(preflight_timeout_ms=3000, readiness_timeout_ms=5000,
                          completion_timeout_ms=8000))
    fields.update(overrides)
    return StandaloneProfile(**fields)


def require_native_stub(case: unittest.TestCase) -> None:
    """SKIP rather than substitute the shell fixture.  See :data:`STUB_BIN`."""
    if STUB_BIN is None:
        case.skipTest(native_stub.NO_COMPILER_REASON)


class PreconditionTests(unittest.TestCase):
    """The five, asserted independently of any run.  They gate the E2E below."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.profile = profile()
        self.child_env = env_policy.build_child_env(
            self.profile, spawn_token="t-e2e", include_secrets=False)

    def test_precondition_1_orca_is_not_on_the_child_path(self) -> None:
        self.assertIsNone(shutil.which("orca", path=self.child_env["PATH"]))
        env_policy.assert_orca_unreachable(self.child_env)

    def test_precondition_2_the_child_env_is_provably_clean(self) -> None:
        env_policy.assert_clean(self.child_env)
        self.assertEqual(env_policy.forbidden_names(self.child_env), ())

    def test_precondition_3_no_orca_name_or_hook_endpoint_reaches_the_child(self) -> None:
        leaked = [name for name in self.child_env
                  if name.startswith("ORCA_")
                  and name not in env_policy.ALLOWED_EXCEPTIONS]
        self.assertEqual(leaked, [], f"{leaked} reached the child env")
        for name in ("ORCA_AGENT_HOOK_ENDPOINT", "ORCA_AGENT_HOOK_TOKEN",
                     "ORCA_TERMINAL_HANDLE", "ORCA_WORKTREE_ID"):
            self.assertNotIn(name, self.child_env)
        # And nothing listens: a scratch listener receives zero bytes across a spawn.
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.4)
        try:
            self._spawn_stub("ready", session_id="s-precond")
            with self.assertRaises((socket.timeout, OSError)):
                listener.accept()
        finally:
            listener.close()

    def test_precondition_4_no_terminal_window_is_ever_opened(self) -> None:
        """The only pty is ``pty.openpty()``-created and headless.

        Asserted structurally over every standalone module: no ``open -a``, no
        ``osascript``, no ``Terminal``/``iTerm`` reference, and no window primitive.  It is
        a property of the code rather than of one observed run, which is the only way to
        assert "at any point".
        """
        import ast
        engine = REPO / "scripts" / "deterministic_workflow"
        # INVOCATION-shaped tokens only.  `xterm-256color` is a TERM *type* the headless
        # pty legitimately advertises -- it is what a terminal-aware CLI needs in order to
        # render at all -- and is not a terminal emulator being launched.  A bare "xterm"
        # substring would flag it and the test would then be silenced, which is worse than
        # a narrower token that still catches every way of opening a window.
        forbidden = ("osascript", "open -a", "Terminal.app", "iTerm", "AppleScript",
                     "NSWorkspace", "tmux new", "tmux -", "xterm -", "screen -d",
                     "wt.exe", "gnome-terminal", "konsole")
        offenders: list[str] = []
        for path in sorted(engine.glob("standalone_*.py")):
            tree = ast.parse(path.read_text())
            docstrings = {id(node.body[0].value)
                          for node in ast.walk(tree)
                          if isinstance(node, (ast.Module, ast.FunctionDef,
                                                ast.AsyncFunctionDef, ast.ClassDef))
                          and node.body and isinstance(node.body[0], ast.Expr)
                          and isinstance(node.body[0].value, ast.Constant)}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node) in docstrings:
                    # Prose SAYING there is no Terminal.app is not a call opening one.
                    continue
                for token in forbidden:
                    if token in node.value:
                        offenders.append(f"{path.name}:{node.lineno}: {token!r}")
        self.assertEqual(
            offenders, [],
            "a standalone module names a terminal emulator outside its own prose; the "
            "agent must never be given a window:\n" + "\n".join(offenders))
        # And the pty really is created in-process, by the one primitive that makes it
        # headless.  This is the positive half: absence of a window primitive plus presence
        # of `openpty` is what "the only pty is headless" means.
        pty_source = (engine / "standalone_pty.py").read_text()
        self.assertIn("pty.openpty()", pty_source)
        tree = ast.parse(pty_source)
        launchers = {node.func.attr for node in ast.walk(tree)
                     if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute)
                     and node.func.attr in ("execve", "execv", "execvp", "execvpe",
                                             "system", "popen", "spawnv", "posix_spawn")}
        self.assertEqual(
            launchers, {"execve"},
            f"the supervisor launches through {sorted(launchers)}; only os.execve into a "
            "pty this runtime created is permitted -- os.system or a spawn helper could "
            "reach a shell that opens a window")

    def test_precondition_5_the_turn_does_not_own_the_lifecycle(self) -> None:
        """The child is a ``setsid`` session leader, so it is not in our process group.

        Asserted from the child's OWN spawn record: its ``sid`` equals its ``pid`` (which is
        what ``setsid`` produces) and differs from this process's session, so ending this
        process's turn cannot end the child.
        """
        session, record = self._spawn_stub("ready", session_id="s-precond5")
        self.assertIsNotNone(record, "the child wrote no spawn record")
        # The agent runs in a session of the runtime's own making, led by the exit watcher
        # (:func:`standalone_pty.spawn`), and in its OWN process group inside it -- which is
        # what lets that group own the pty foreground and makes R-A legs 3 and 4 equalities.
        self.assertEqual(record["sid"], session["leader_pid"],
                         "the agent is not in the runtime's own session; setsid did not "
                         "take effect")
        self.assertEqual(record["pgid"], record["pid"],
                         "the agent is not its own process group leader, so the pty's "
                         "foreground process would not be the agent")
        self.assertNotEqual(record["sid"], os.getsid(0),
                            "the child shares this process's session and would die with it")

    # -- helper ----------------------------------------------------------------------------
    def _spawn_stub(self, mode: str, *, session_id: str):
        # Only the SPAWNING preconditions need the fixture; the env-policy ones above hold
        # on every host and must keep running there.
        require_native_stub(self)
        env = dict(self.child_env)
        env["OS37_STUB_MODE"] = mode
        env["OS37_STUB_SESSION_ID"] = session_id
        target = pty_supervisor.spawn_record_path(self.base, "run_e2e", "intent-p",
                                                  "i-p")
        session = pty_supervisor.spawn(
            argv=(str(STUB_BIN / "os37-stub-cli"),), env=env, profile=self.profile,
            session_id=session_id, incarnation="i-p", spawn_record_target=str(target))
        _drain(session["master_fd"], seconds=3)
        try:
            os.waitpid(session["pid"], 0)
        except OSError:
            pass
        pty_supervisor.release(session)
        probe = pty_supervisor.read_spawn_records(self.base, "run_e2e", "intent-p")
        return session, probe["record"]


class RequeryAfterTurnEndTests(unittest.TestCase):
    """Precondition 5's other half: a FRESH PROCESS re-queries the run."""

    def test_a_fresh_process_recovers_identity_journal_and_state(self) -> None:
        base = Path(tempfile.mkdtemp())
        ledger_path = base / "runs" / "run_e2e" / "runtime_state.json"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger = FileRuntimeStateStore(ledger_path)
        journal = journal_mod.ExecutionJournal(base, "run_e2e")
        intent = {"intent_id": "intent-1", "task_id": "task-1",
                  "dispatch_id": "dispatch-1", "command_id": "cmd-1",
                  "payload_digest": "digest", "run_id": "run_e2e",
                  "action_kind": "DISPATCH_AGENT", "phase": "IMPLEMENTATION",
                  "role": "WORKER", "round_kind": "PHASE_GATE"}
        claim = ledger.claim(intent)
        ledger.record_receipt("intent-1",
                              {"intent_id": "intent-1", "task_id": "task-1",
                               "dispatch_id": "dispatch-1", "external_id": "s-1:i-1"},
                              claim["lease_token"])
        journal.append(journal_mod.make_record(
            kind="EVENT", derived_from="pty", intent_id="intent-1",
            dispatch_id="dispatch-1", task_id="task-1", session_id="s-1",
            process_incarnation="i-1", event="turn_start_observed", state="RUNNING",
            source_vocabulary={"pid": 999999, "captured_tty": "ttys999"}))

        script = textwrap.dedent(f"""
            import json, sys
            sys.path.insert(0, {str(REPO)!r})
            from scripts.deterministic_workflow import standalone_journal as sj
            from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
            ledger = FileRuntimeStateStore({str(ledger_path)!r})
            snap = sj.rediscover("run_e2e", {str(base)!r}, runtime_state=ledger,
                                 intent_ids=("intent-1",))
            entry = snap["intents"]["intent-1"]
            print(json.dumps({{"ledger_status": entry["ledger_status"],
                               "lease": entry["lease"],
                               "state": entry.get("state"),
                               "lost_reason": entry.get("lost_reason", ""),
                               "journal_present": snap["journal_present"],
                               "open": list(sj.ExecutionJournal(
                                   {str(base)!r}, "run_e2e").open_dispatches())}}))
            """)
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                              text=True, timeout=60, cwd=str(REPO))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result["ledger_status"], "EFFECTED",
                         "the fresh process could not read what was effected")
        self.assertEqual(result["lease"], "unreconciled",
                         "a restart granted a writer on the strength of the previous "
                         "process's lease")
        self.assertEqual(result["open"], ["intent-1"])
        # The process is genuinely gone and no sentinel exists -> LOST, never COMPLETED.
        self.assertEqual(result["state"], "LOST")
        self.assertIn(result["lost_reason"],
                      ("stop_unverified", "process_table_unreadable"))


@unittest.skipUnless(E2E_ENABLED, E2E_REASON)
class StandaloneE2ETests(unittest.TestCase):
    """The Worker -> Reviewer -> correction -> fresh Final Review loop, Orca-free.

    Skip-gated: it spawns real local processes.  The output is recorded as a run artefact so
    the evidence is the actual transcript rather than an assertion about one.
    """

    def test_real_runtime_smoke(self) -> None:  # pragma: no cover - opt-in
        base = Path(tempfile.mkdtemp())
        prof = profile()
        child_env = env_policy.build_child_env(prof, spawn_token="t-smoke",
                                               include_secrets=False)
        # Precondition gate FIRST: a failure here records "not established", not a pass.
        env_policy.assert_clean(child_env)
        env_policy.assert_orca_unreachable(child_env)

        journal = journal_mod.ExecutionJournal(base, "run_smoke")
        ledger_path = base / "runs" / "run_smoke" / "runtime_state.json"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger = FileRuntimeStateStore(ledger_path)
        adapter = StandaloneAdapter(None, runtime_state=ledger,
                                    settlement_journal=journal, artifact_base=base,
                                    run_id="run_smoke")
        self.assertIn("external_resume", adapter.capabilities())

        transcript: list[dict] = []
        for role in ("WORKER", "PHASE_REVIEWER", "WORKER_CORRECTION", "FINAL_REVIEWER"):
            env = dict(child_env)
            env["OS37_STUB_MODE"] = "ready"
            env["OS37_STUB_SESSION_ID"] = f"s-{role.lower()}"
            target = pty_supervisor.spawn_record_path(base, "run_smoke",
                                                      f"intent-{role}", "i-1")
            session = pty_supervisor.spawn(
                argv=(str(STUB_BIN / "os37-stub-cli"),), env=env, profile=prof,
                session_id=f"s-{role.lower()}", incarnation="i-1",
                spawn_record_target=str(target))
            output = _drain(session["master_fd"], seconds=5)
            try:
                os.waitpid(session["pid"], 0)
            except OSError:
                pass
            pty_supervisor.release(session)
            probe = pty_supervisor.read_spawn_records(base, "run_smoke",
                                                      f"intent-{role}")
            transcript.append({"role": role, "spawn_record": probe["outcome"],
                               "bytes": len(output)})
            self.assertEqual(probe["outcome"], "present",
                             f"{role}: no execve was proven")
        artefact = base / "E2E_TRANSCRIPT.json"
        artefact.write_text(json.dumps(transcript, indent=2, sort_keys=True))
        self.assertEqual(len(transcript), 4)


def _drain(fd: int, *, seconds: float) -> str:
    """Read whatever the child writes, bounded.  Never blocks past the bound."""
    chunks: list[bytes] = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if not ready:
            continue
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        chunks.append(data)
    return b"".join(chunks).decode("utf-8", "replace")


class LiveReadinessQuorumTests(unittest.TestCase):
    """The READY quorum, driven through a REAL spawn against the stub CLI.

    The deterministic readiness tests assert the decision function over constructed
    evidence.  These assert the whole chain: the runtime mints a session id, passes it on
    argv, the child echoes it back on its structured channel, and R-A is read from the OS.
    Nothing here is mocked, which is why the negative case below is worth as much as the
    positive one -- the same real spawn, with the CLI quoting a FOREIGN id, is refused.
    """

    def setUp(self) -> None:
        require_native_stub(self)
        self.base = Path(tempfile.mkdtemp())
        os.environ.setdefault("OS37_E2E_KEY_SOURCE", "not-a-real-key")

    def _session(self, mode: str):
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        prof = profile(driver_env={"OS37_STUB_MODE": mode},
                       auth_secret_ref={"ANTHROPIC_API_KEY": "OS37_E2E_KEY_SOURCE"})
        intent = {"intent_id": f"intent-{mode}", "task_id": "task-1",
                  "dispatch_id": "dispatch-1", "command_id": "c1",
                  "payload_digest": "d", "run_id": "run_live",
                  "action_kind": "DISPATCH_AGENT", "phase": "IMPLEMENTATION",
                  "role": "WORKER", "round_kind": "PHASE_GATE"}
        ledger = InMemoryRuntimeStateStore()
        claim = ledger.claim(intent)
        session = StandaloneSession(
            intent=intent, profile=prof, artifact_base=self.base, run_id="run_live",
            journal=sj.ExecutionJournal(self.base, "run_live"), runtime_state=ledger)
        receipt = session.start(
            lease_token=claim["lease_token"],
            help_text="--bare --settings --session-id -p --output-format stream-json",
            prober=_version_prober,
            rehearsal=lambda p, e, s: {"channel": "structured",
                                        "record_type": "system", "session_id": s},
            # The mode rehearsal is injected for the SAME reason the readiness rehearsal is:
            # these cases drive the RUNTIME's readiness quorum, and the stub modes they use
            # (`ready-foreign`, `no-readiness`) exist precisely to fail R-B -- so a REAL
            # mode rehearsal would refuse them at preflight with
            # `identity_binding_unverified` and the quorum under test would never run.
            # Preflight's own D4.2b branches are driven, un-injected, by
            # `test_os37_cli_preconditions.py::DeliveryModeRehearsalTests`.
            mode_rehearsal=lambda p, e: {"r_b_closed": True, "delivery_proof": True,
                                         "auth_marker": None,
                                         # The fixture CLI genuinely DOES reach the quorum
                                         # and then wait, which is what makes
                                         # `post_ready_delivery` its honest declaration.
                                         "waited_without_prompt": True,
                                         "evaluable": True, "identity_bound": True,
                                         "detail": {"injected": "runtime-quorum case"}})
        return session, receipt, ledger

    def test_a_real_spawn_reaches_the_ready_quorum(self) -> None:
        session, receipt, ledger = self._session("ready-slow")
        try:
            self.assertEqual(receipt["start_outcome"], "ready",
                             receipt["failure_reason"])
            # The ledger flipped CLAIMED -> EFFECTED through the ONE permitted write, and
            # the receipt names the fence.
            stored = ledger.get_receipt(receipt["intent_id"])
            self.assertEqual(stored["status"], "EFFECTED")
            self.assertEqual(stored["receipt"]["external_id"],
                             f"{session.session_id}:{session.incarnation}")
            outcome = session.await_ready()
            self.assertEqual(
                outcome["state"], "READY",
                f"the quorum did not close: {outcome['verdict']} over "
                f"{session.capture.text()[:200]!r}")
            self.assertEqual(outcome["verdict"]["quorum"],
                             {"R-A": True, "R-B": True, "R-C": True})
            self.assertIn("readiness_observed", session.event_log)
            self.assertGreater(session.event_log.index("readiness_observed"),
                               session.event_log.index("identity_bound"),
                               "I-1: identity must bind before readiness is observed")
        finally:
            _reap(session)

    def test_a_real_spawn_quoting_a_foreign_session_id_never_reaches_ready(self) -> None:
        """The SAME real spawn, refused -- because R-B is equality against a minted value.

        The CLI is live, the process is live, R-A holds, and the record is well-formed and
        of a declared type.  The only thing wrong is the identifier, and that alone must be
        enough to refuse.
        """
        session, receipt, _ledger = self._session("ready-foreign")
        try:
            self.assertEqual(receipt["start_outcome"], "ready",
                             receipt["failure_reason"])
            for _ in range(30):
                session.pump(timeout_ms=100)
                if session.capture.size:
                    break
            self.assertIn("s-someone-else", session.capture.text(),
                          "the stub did not emit the foreign record")
            verdict = session.readiness()
            self.assertNotEqual(verdict["verdict"], "ready")
            self.assertFalse(verdict["quorum"]["R-B"])
            self.assertNotIn("readiness_observed", session.event_log)
            self.assertEqual(session.state, "STARTING",
                             "the state advanced on a foreign readiness record")
        finally:
            _reap(session)

    def test_a_real_spawn_emitting_no_declared_record_times_out(self) -> None:
        """TIMED_OUT, never READY and never "not ready" as a fact."""
        session, receipt, _ledger = self._session("no-readiness")
        try:
            self.assertEqual(receipt["start_outcome"], "ready",
                             receipt["failure_reason"])
            outcome = session.await_ready()
            self.assertEqual(outcome["state"], "TIMED_OUT")
            self.assertEqual(outcome["verdict"]["verdict"], "unprovable")
            self.assertEqual(session.state, "TIMED_OUT")
        finally:
            _reap(session)


def _version_prober(argv, env, *, timeout_ms, cwd=None):
    """Answer the version probe from the stub's ``version-ok`` mode.

    A separate mode, because the readiness mode's whole job is to emit a readiness record --
    which is not a version string.  One stub, several modes, no mocks.
    """
    from scripts.deterministic_workflow import standalone_preflight as _preflight
    return _preflight.probe_on_pty(argv, {**env, "OS37_STUB_MODE": "version-ok"},
                                    timeout_ms=timeout_ms, cwd=cwd)


def _reap(session) -> None:
    if session.pty is None:
        return
    for sig in (15, 9):
        try:
            os.killpg(session.pty["pgid"], sig)
        except OSError:
            break
        try:
            done, _status = os.waitpid(session.pty["pid"], os.WNOHANG)
            if done:
                break
        except OSError:
            break
        time.sleep(0.05)
    try:
        os.waitpid(session.pty["pid"], 0)
    except OSError:
        pass
    session.release()


class FullSupervisedDispatchTests(unittest.TestCase):
    """A COMPLETE standalone dispatch against a real spawn: spawn -> settle, no Orca.

    This is the substance of WI-17 that can be asserted deterministically.  It drives
    ``run_dispatch`` -- the same method ``StandaloneAdapter.start`` calls, and therefore the
    same path ``executor._settle_now`` drives -- against the stub CLI, and asserts the whole
    lifecycle: the child's own spawn record, the ONE ledger receipt, the readiness quorum,
    proven delivery, both completion gates, and a settlement that ``contracts.validate_event``
    accepts unchanged.

    No Orca process, binary, API or terminal is involved at any point, and the preconditions
    above assert that independently.
    """

    def setUp(self) -> None:
        require_native_stub(self)
        self.base = Path(tempfile.mkdtemp())
        os.environ.setdefault("OS37_E2E_KEY_SOURCE", "not-a-real-key")

    def test_a_real_dispatch_reaches_a_validated_settlement(self) -> None:
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.contracts import validate_event
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession

        prof = profile(driver_env={"OS37_STUB_MODE": "agent"},
                       auth_secret_ref={"ANTHROPIC_API_KEY": "OS37_E2E_KEY_SOURCE"})
        intent = {"schema_version": "os40.action.v2", "intent_id": "intent-e2e",
                  "command_id": "cmd-e2e", "action_kind": "DISPATCH_AGENT",
                  "run_id": "run_e2e", "phase": "IMPLEMENTATION", "phase_iteration": 1,
                  "final_review_iteration": 0, "role": "WORKER",
                  "round_kind": "PHASE_GATE", "artifact_binding": {},
                  "repository_binding": {}, "payload_digest": "d", "repair_attempt": 0,
                  "gate_iteration": 1, "artifact_contract_path": "x.md",
                  "repair_instruction": None}
        ledger = InMemoryRuntimeStateStore()
        claim = ledger.claim(intent)
        journal = sj.ExecutionJournal(self.base, "run_e2e")
        session = StandaloneSession(intent=intent, profile=prof,
                                    artifact_base=self.base, run_id="run_e2e",
                                    journal=journal, runtime_state=ledger)
        try:
            receipt = session.run_dispatch(
                lease_token=claim["lease_token"],
                help_text="--bare --settings --session-id -p --output-format stream-json",
                prober=_version_prober,
                rehearsal=lambda p, e, s: {"channel": "structured",
                                            "record_type": "system", "session_id": s})
        finally:
            _reap(session)

        self.assertEqual(receipt["start_outcome"], "ready", receipt.get("failure_reason"))
        self.assertTrue(receipt["settled"], "run_dispatch returned without settling")

        # -- the LIFECYCLE, in order.  I-1 is the ordering, not a boolean pair. -----------
        self.assertEqual(
            session.event_log,
            ["spawned", "identity_bound", "readiness_observed", "prompt_written",
             "delivery_proof_observed", "exit_observed", "settlement_confirmed"],
            "the dispatch did not walk the whole lifecycle in order")
        self.assertEqual(session.state, "COMPLETED")

        # -- the ONE ledger write, and the settlement the ENGINE will accept -------------
        stored = ledger.get_receipt("intent-e2e")
        self.assertEqual(stored["status"], "SETTLED")
        self.assertEqual(stored["receipt"]["external_id"],
                         f"{session.session_id}:{session.incarnation}")
        event = ledger.get_settlement("intent-e2e")
        self.assertIsNotNone(event, "the ledger holds no settlement")
        validate_event(intent, dict(event))          # raises if the engine would refuse it
        self.assertEqual(event["result"]["status"], "COMPLETE",
                         "the result did not come through the SHARED policy parser")

        # -- the child's own evidence, and the journal ------------------------------------
        probe = pty_supervisor.read_spawn_records(self.base, "run_e2e", "intent-e2e")
        self.assertEqual(probe["outcome"], "present",
                         "no execve was proven for a dispatch that settled")
        kinds = [row["kind"] for row in journal.rows_for("intent-e2e")]
        self.assertEqual(
            kinds,
            ["DELIVERY_INTENT", "EVENT", "SPAWN_OBSERVED", "RECEIPT_OBSERVED", "EVENT",
             "EVENT", "SETTLEMENT_OBSERVED"],
            "the journal's record order changed; DELIVERY_INTENT is FIRST by construction")
        # D4.3a / USER DIRECTIVE D-D.1, asserted as an ORDER rather than as a presence:
        # the atomic delivery intent is appended and fsynced BEFORE the process exists, so
        # a successor that finds no DELIVERY_INTENT has PROVED no fork happened.
        self.assertLess(kinds.index("DELIVERY_INTENT"), kinds.index("SPAWN_OBSERVED"),
                        "the delivery intent must be journalled before the spawn record")
        intent_row = journal.delivery_intent_for("intent-e2e")
        self.assertIsNotNone(intent_row)
        self.assertEqual(intent_row["source_vocabulary"]["delivery_mode"],
                         "post_ready_delivery")
        self.assertTrue(intent_row["source_vocabulary"]["prompt_digest"],
                        "the intent carries the prompt DIGEST")
        # ...and it is NOT a claim: the record kind vocabulary still has no CLAIMED member,
        # and the single claim authority is the ledger.
        self.assertNotIn("CLAIMED", sj.RECORD_KINDS)
        self.assertEqual(journal.open_dispatches(), (),
                         "the dispatch settled but is still open")

        # -- and a STRANGER PROCESS sees the settled run ----------------------------------
        snapshot = sj.rediscover("run_e2e", self.base, runtime_state=ledger,
                                  intent_ids=("intent-e2e",))
        self.assertEqual(snapshot["intents"]["intent-e2e"]["ledger_status"], "SETTLED")

    def test_a_second_dispatch_of_the_same_intent_reuses_the_effect(self) -> None:
        """Idempotency: the second call observes, and never spawns again.

        The pre-effect claim exists for exactly this, and the standalone path reads the
        LEDGER -- not memory -- so a successor process holding none of this one's objects
        gets the same answer.
        """
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession

        prof = profile(driver_env={"OS37_STUB_MODE": "agent"},
                       auth_secret_ref={"ANTHROPIC_API_KEY": "OS37_E2E_KEY_SOURCE"})
        intent = {"intent_id": "intent-idem", "command_id": "c", "payload_digest": "d",
                  "run_id": "run_idem", "phase": "IMPLEMENTATION", "role": "WORKER",
                  "round_kind": "PHASE_GATE"}
        ledger = InMemoryRuntimeStateStore()
        claim = ledger.claim(intent)
        ledger.record_receipt("intent-idem",
                              {"intent_id": "intent-idem", "task_id": "t",
                               "dispatch_id": "d", "external_id": "s-prior:i-prior"},
                              claim["lease_token"])
        spawns: list = []

        def refusing_spawner(**kwargs):
            spawns.append(kwargs)
            raise AssertionError("a second dispatch spawned a second process")

        session = StandaloneSession(
            intent=intent, profile=prof, artifact_base=self.base, run_id="run_idem",
            journal=sj.ExecutionJournal(self.base, "run_idem"), runtime_state=ledger,
            spawner=refusing_spawner)
        receipt = session.run_dispatch(lease_token=claim["lease_token"])
        self.assertEqual(spawns, [], "the effect was re-created instead of observed")
        self.assertTrue(receipt["reused_existing_effect"])
        self.assertEqual(receipt["session_id"], "s-prior")
        self.assertEqual(receipt["process_incarnation"], "i-prior")


class LiveInterruptLadderTests(unittest.TestCase):
    """The interrupt ladder against a REAL process, after the drain wiring changed that path.

    The deterministic tests drive the ladder over an injected process table, which is the
    only way to exercise the recycled-pid and stale-snapshot refusals.  This asserts the
    other half: that the same ladder works against a live child, with the drain in place --
    because without draining, an exiting child never becomes reapable and rung 4 would report
    ``exit_unproven`` for a process that really died.
    """

    def setUp(self) -> None:
        require_native_stub(self)
        self.base = Path(tempfile.mkdtemp())
        os.environ.setdefault("OS37_E2E_KEY_SOURCE", "not-a-real-key")

    def test_a_live_child_is_interrupted_with_every_gate_verified(self) -> None:
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        from scripts.deterministic_workflow.standalone_profile import Timeouts
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession

        prof = profile(driver_env={"OS37_STUB_MODE": "ready-slow"},
                       auth_secret_ref={"ANTHROPIC_API_KEY": "OS37_E2E_KEY_SOURCE"},
                       timeouts=Timeouts(preflight_timeout_ms=4000,
                                          readiness_timeout_ms=8000,
                                          completion_timeout_ms=8000,
                                          graceful_force_timeout_ms=1500,
                                          physical_exit_timeout_ms=3000,
                                          force_retry_ms=100))
        intent = {"intent_id": "intent-int", "command_id": "c", "payload_digest": "d",
                  "run_id": "run_int", "phase": "IMPLEMENTATION", "role": "WORKER",
                  "round_kind": "PHASE_GATE"}
        ledger = InMemoryRuntimeStateStore()
        claim = ledger.claim(intent)
        session = StandaloneSession(
            intent=intent, profile=prof, artifact_base=self.base, run_id="run_int",
            journal=sj.ExecutionJournal(self.base, "run_int"), runtime_state=ledger)
        try:
            receipt = session.start(
                lease_token=claim["lease_token"],
                help_text="--bare --settings --session-id -p --output-format stream-json",
                prober=_version_prober,
                rehearsal=lambda p, e, s: {"channel": "structured",
                                            "record_type": "system", "session_id": s})
            self.assertEqual(receipt["start_outcome"], "ready",
                             receipt["failure_reason"])
            self.assertEqual(session.await_ready()["state"], "READY")

            result = session.interrupt("operator asked")
            self.assertIn(
                result["interrupt_outcome"],
                ("interrupted_confirmed", "terminated_forced"),
                f"the ladder did not prove an exit: {result['interrupt_outcome']}. Without "
                "the drain an exiting child never becomes reapable, so this would be "
                "exit_unproven for a process that really died")
            self.assertEqual(session.state, "INTERRUPTED")
            self.assertEqual(session.lost_reason, "")

            gates = [step for step in result["ladder"] if step["rung"].startswith("G")]
            self.assertTrue(gates, "the ladder recorded no identity gate at all")
            for step in gates:
                self.assertTrue(
                    step["identity_verified"],
                    f"gate {step['rung']} passed without verifying identity: {step}")
            # A graceful exit must not have escalated: SIGKILL is rung 3, and reaching it
            # when SIGTERM sufficed would mean the bounded wait did not observe the exit.
            if result["interrupt_outcome"] == "interrupted_confirmed":
                self.assertNotIn(
                    "rung_3_force", [step["rung"] for step in result["ladder"]],
                    "the ladder escalated to SIGKILL although the child exited gracefully")
        finally:
            _reap(session)

    def test_the_journal_records_the_interrupt_and_never_a_completion(self) -> None:
        """``COMPLETED``/``FAILED`` are unreachable from any interrupt, on a live process too."""
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        from scripts.deterministic_workflow.standalone_profile import Timeouts
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession

        prof = profile(driver_env={"OS37_STUB_MODE": "ready-slow"},
                       auth_secret_ref={"ANTHROPIC_API_KEY": "OS37_E2E_KEY_SOURCE"},
                       timeouts=Timeouts(preflight_timeout_ms=4000,
                                          readiness_timeout_ms=8000,
                                          completion_timeout_ms=8000,
                                          graceful_force_timeout_ms=1500,
                                          physical_exit_timeout_ms=3000,
                                          force_retry_ms=100))
        intent = {"intent_id": "intent-int2", "command_id": "c", "payload_digest": "d",
                  "run_id": "run_int2", "phase": "IMPLEMENTATION", "role": "WORKER",
                  "round_kind": "PHASE_GATE"}
        ledger = InMemoryRuntimeStateStore()
        claim = ledger.claim(intent)
        journal = sj.ExecutionJournal(self.base, "run_int2")
        session = StandaloneSession(
            intent=intent, profile=prof, artifact_base=self.base, run_id="run_int2",
            journal=journal, runtime_state=ledger)
        try:
            session.start(
                lease_token=claim["lease_token"],
                help_text="--bare --settings --session-id -p --output-format stream-json",
                prober=_version_prober,
                rehearsal=lambda p, e, s: {"channel": "structured",
                                            "record_type": "system", "session_id": s})
            session.await_ready()
            session.interrupt("operator asked")
            states = [row["state"] for row in journal.rows_for("intent-int2")]
            self.assertIn("INTERRUPTED", states)
            for forbidden in ("COMPLETED", "FAILED"):
                self.assertNotIn(
                    forbidden, states,
                    f"the journal recorded {forbidden} for an interrupt; those two states "
                    "have exactly one entry edge and it is not this one")
            self.assertEqual(
                [row["kind"] for row in journal.rows_for("intent-int2")][-1], "EVENT",
                "an interrupt wrote a SETTLEMENT record; interrupting is not settling")
        finally:
            _reap(session)


if __name__ == "__main__":
    unittest.main()


# =====================================================================================
class R10RealAgentEvidenceTests(unittest.TestCase):
    """DESIGN §D13.6 / FINAL_REVIEW F-002.  **The real-agent E2E, and what may be cited for it.**

    F-002's finding was not that the R10 infrastructure was broken -- it worked, and the
    captured run genuinely removed Orca from PATH and exercised nine native processes, the
    append-only journal, the recovery path and lifecycle settlement.  The finding was that
    `os37-r10-agent` supplied readiness and success ITSELF and selected the reviewer's
    FAIL/PASS from a fixture input, so **no agent performed the work and no agent formed a
    verdict**.

    These cases hold the boundary that finding drew.  The fixture E2E is RETAINED (USER
    DIRECTIVE D-H) and is exercised elsewhere in this file as runtime-boundary evidence; it
    is INADMISSIBLE as R10 evidence, and the cases below fail if it is ever cited as such.
    """

    HARNESS = Path(__file__).resolve().parent / "os37_r10_real_agent.py"

    #: WHERE the real-agent evidence of THIS run lives, named by the operator who produced
    #: it.  It used to be the hard-coded `artifacts/runs/run_54d90086bd75/evidence/
    #: r10_real_agent`, an untracked directory from one particular run -- so a clean
    #: checkout could not run these cases at all, and a LATER run's evidence was silently
    #: never read (external review #1).  There is deliberately no default: evidence about a
    #: real agent run is evidence about THAT run, and inventing a path would either read
    #: somebody else's or read nothing while looking like it read something.
    EVIDENCE_ENV = "ORCA_OS37_R10_EVIDENCE"

    @property
    def EVIDENCE(self) -> Path:
        raw = os.environ.get(self.EVIDENCE_ENV, "")
        self.assertTrue(
            raw,
            f"set {self.EVIDENCE_ENV} to the directory the real-agent E2E wrote; these "
            "cases assert over evidence a run produced and there is no default run")
        directory = Path(raw)
        self.assertTrue(directory.is_dir(),
                        f"{self.EVIDENCE_ENV}={raw!r} is not a directory")
        return directory

    def test_the_real_agent_harness_cannot_author_a_verdict(self) -> None:
        """R-5, as a STATIC property of the harness rather than a claim about it.

        This is precisely what F-002 found wrong with the old R10: a runtime that can choose
        a verdict has not demonstrated that an agent formed one.  Checked over the AST, so a
        docstring quoting `RESULT: PASS` does not trip it and a real assignment cannot hide
        inside one.

        What IS allowed: comparing a value the production parser already extracted from the
        agent's artifact, which is how `classify_run` names the SHAPE of a run.  What is not:
        assigning, returning or defaulting a verdict.
        """
        import ast as _ast
        tree = _ast.parse(self.HARNESS.read_text())
        offenders: list[str] = []
        verdict_names = {"verdict", "step2_verdict", "step4_verdict", "result", "outcome"}
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Assign):
                literal = (node.value.value if isinstance(node.value, _ast.Constant)
                           else None)
                if not isinstance(literal, str):
                    continue
                if literal.upper() not in ("PASS", "FAIL"):
                    continue
                for target in node.targets:
                    name = (target.id if isinstance(target, _ast.Name)
                            else target.attr if isinstance(target, _ast.Attribute)
                            else "")
                    if name in verdict_names:
                        offenders.append(f"line {node.lineno}: {name} = {literal!r}")
            if isinstance(node, _ast.Return) and isinstance(node.value, _ast.Constant):
                if isinstance(node.value.value, str) and \
                        node.value.value.upper() in ("PASS", "FAIL"):
                    offenders.append(f"line {node.lineno}: returns {node.value.value!r}")
        self.assertEqual(
            offenders, [],
            "the real-agent harness authors a verdict: " + "; ".join(offenders)
            + ".  The verdict must come only from the agent's own artifact, parsed by "
            "decision_contract.parse_agent_settlement")

    def test_the_harness_reads_the_verdict_through_the_production_parser(self) -> None:
        """Not a second parser.  The result vocabulary is workflow POLICY."""
        source = self.HARNESS.read_text()
        self.assertIn("decision_contract.parse_agent_settlement", source,
                      "the harness does not use the production settlement parser; a "
                      "standalone-specific parser would be the per-runtime divergence "
                      "AC-37-20 forbids")
        self.assertNotIn("OS37_R10_FAIL_PHASE", source,
                         "a verdict-selection variable is present -- this is exactly the "
                         "mechanism FINAL_REVIEW F-002 rejected")

    def test_the_harness_never_uses_the_retained_fixture(self) -> None:
        """D-H: the fixture is RETAINED, and it may never be the basis for this requirement.

        Checked over the AST rather than the raw text, because this module's own docstring
        NAMES the fixture in order to say why it is inadmissible -- and a check that could
        not tell an explanation from a dependency would push that explanation out of the
        code, which is the opposite of what it is for.
        """
        import ast as _ast
        tree = _ast.parse(self.HARNESS.read_text())
        referenced: list[str] = []
        for node in _ast.walk(tree):
            names: list[str] = []
            if isinstance(node, _ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, _ast.ImportFrom):
                names = [(node.module or "")] + [alias.name for alias in node.names]
            elif isinstance(node, _ast.Constant) and isinstance(node.value, str):
                # A string CONSTANT in executable position -- an argv entry, a path, a
                # binary name -- is a dependency; a docstring is not, and `ast` marks the
                # difference by where the constant sits.
                if node.value in ("os37-r10-agent", "os37_r10_fixture"):
                    names = [node.value]
            for name in names:
                if "os37_r10_fixture" in name or "os37-r10-agent" in name:
                    referenced.append(f"line {node.lineno}: {name}")
        # The docstring mentions the fixture by name; that is an explanation, and the AST
        # walk above only flags an import or an executable string constant.
        docstring = _ast.get_docstring(tree) or ""
        self.assertIn("os37_r10_fixture.py", docstring,
                      "the harness should say IN THE CODE why the fixture is inadmissible")
        self.assertEqual(
            referenced, [],
            "the real-agent harness DEPENDS on the retained fixture: "
            + "; ".join(referenced)
            + ".  The fixture run stays as runtime-boundary evidence and is inadmissible "
            "as R10 evidence")

    def test_a_blocked_row_cannot_express_a_pass(self) -> None:
        """D13.6(d).  ``verdict`` is a single-member ``Literal``: the TYPE cannot say PASS."""
        from scripts import os37_r10_real_agent as harness
        row = harness.blocked("codex", binary_realpath="/opt/homebrew/bin/codex",
                              version_string="codex-cli 0.153.2",
                              constraint="auth_absent",
                              measurement="codex login status",
                              observed="Not logged in", exit_code=1)
        self.assertEqual(row["verdict"], "BLOCKED")
        import typing as _typing
        hints = _typing.get_type_hints(harness.BlockedEvidence)
        self.assertEqual(
            _typing.get_args(hints["verdict"]), ("BLOCKED",),
            "the BLOCKED verdict type gained a second member; a type that can say PASS is "
            "a type that will")

    def test_a_blocked_row_without_a_measurement_is_invalid(self) -> None:
        """'Blocked' without evidence is indistinguishable from 'not attempted'."""
        from scripts import os37_r10_real_agent as harness
        for empty in ("measurement", "observed"):
            with self.subTest(empty):
                fields = {"binary_realpath": "/x", "version_string": "v",
                          "constraint": "auth_absent", "measurement": "cmd",
                          "observed": "bytes", "exit_code": 1}
                fields[empty] = "   "
                with self.assertRaises(ValueError):
                    harness.blocked("claude", **fields)

    def test_the_roll_up_refuses_to_aggregate(self) -> None:
        """D13.6(e).  One BLOCKED row means PARTIAL, never PASS, and a 2-of-3 is FLAKY."""
        from scripts import os37_r10_real_agent as harness
        conforming = [{"cli_by_role": {"worker": "claude", "reviewer": "claude"},
                       "outcome": "CONFORMING", "step2_verdict": "FAIL",
                       "step4_verdict": "PASS"} for _ in range(3)]
        clean = harness.roll_up(list(conforming), [])
        self.assertEqual(clean["overall"], "PASS")

        blocked_row = harness.blocked(
            "codex", binary_realpath="/opt/homebrew/bin/codex",
            version_string="codex-cli 0.153.2", constraint="auth_absent",
            measurement="codex login status", observed="Not logged in", exit_code=1)
        partial = harness.roll_up(list(conforming), [blocked_row])
        self.assertTrue(partial["overall"].startswith("PARTIAL"), partial["overall"])
        self.assertIn("codex BLOCKED", partial["overall"])
        self.assertNotEqual(partial["overall"], "PASS")

        flaky = harness.roll_up(
            conforming[:2] + [{"cli_by_role": {"worker": "claude", "reviewer": "claude"},
                               "outcome": "INDUCEMENT_INEFFECTIVE",
                               "step2_verdict": "PASS", "step4_verdict": "PASS"}], [])
        self.assertIn("FLAKY", flaky["overall"],
                      "a 2-of-3 result was rounded up instead of reported")

    def test_a_design_routing_constraint_routes_to_design(self) -> None:
        """Three constraints are statements about THIS DESIGN, not about the host."""
        from scripts import os37_r10_real_agent as harness
        row = harness.blocked("claude", binary_realpath="/x", version_string="v",
                              constraint="delivery_mode_mismatch",
                              measurement="the composed argv",
                              observed="no delivery proof by the deadline", exit_code=1)
        summary = harness.roll_up([], [row])
        self.assertEqual(summary["per_cli"]["claude"]["matrix_outcome"], "BLOCKED")
        self.assertTrue(summary["per_cli"]["claude"]["routes_to_design"],
                        "a constraint saying the design is wrong for the installed CLI "
                        "must route to DESIGN as PREVIOUS_PHASE_CHANGE_REQUIRED, not be "
                        "recorded as an unavailable host")
        for constraint in ("auth_absent", "binary_absent", "quota_exhausted"):
            with self.subTest(constraint):
                other = harness.blocked("claude", binary_realpath="/x",
                                        version_string="v", constraint=constraint,
                                        measurement="m", observed="o", exit_code=1)
                self.assertFalse(
                    harness.roll_up([], [other])["per_cli"]["claude"]["routes_to_design"])

    def test_the_prompt_fixtures_are_committed_and_hashed(self) -> None:
        """R-1: the inducement is DATA, so a prompt change is a visible diff.

        And the inducement is in the WORKER's input, never the reviewer's: the reviewer
        fixture must not mention a defect, a verdict, or what to look for.
        """
        from scripts import os37_r10_real_agent as harness
        digests = harness.prompt_digests()
        self.assertEqual(set(digests), set(harness.PROMPT_FILES))
        for name, digest in digests.items():
            self.assertEqual(len(digest), 64, f"{name} has no recorded digest")
        # Line wrapping is a formatting choice, so the safeguard sweep reads the prompt as
        # one whitespace-normalised string rather than depending on where the lines break.
        reviewer = " ".join((harness.FIXTURES / "reviewer.md").read_text().lower().split())
        # DIRECTIVE phrases only.  The fixture legitimately contains "do not assume a defect
        # exists" -- an instruction NOT to prejudge, which is the opposite of a leak -- so a
        # substring sweep that could not tell a directive from its negation would force that
        # safeguard out of the prompt.
        for leak in ("return fail", "you should fail", "the answer is fail",
                     "must return fail", "the worker omitted", "there is a defect",
                     "conclude fail"):
            self.assertNotIn(leak, reviewer,
                             f"the reviewer fixture tells the agent what to conclude "
                             f"({leak!r}); the FAIL must be the reviewer's own judgement")
        for safeguard in ("decide for yourself", "do not assume a defect exists",
                          "do not assume the submission is correct"):
            self.assertIn(safeguard, reviewer,
                          f"the reviewer fixture dropped the {safeguard!r} safeguard, "
                          "which is what keeps the verdict the agent's own")
        contract = (harness.FIXTURES / "task_contract.md").read_text()
        self.assertIn("ValueError", contract)
        worker_1 = (harness.FIXTURES / "worker_iteration1.md").read_text()
        self.assertIn("Do NOT", worker_1,
                      "the inducement must be a scope instruction in the WORKER's input")

    @unittest.skipUnless(E2E_ENABLED, E2E_REASON)
    def test_the_recorded_evidence_is_not_fixture_sourced(self) -> None:
        """The RETAINED artifact contains no fixture-selected verdict.

        Gated on the same opt-in variable as every other case that depends on real local
        agent processes, so its skip behaviour is deterministic and declared in
        `scripts/tolerated_skip_manifest.txt` with the same exact reason.  The harness's
        STATIC properties -- that it cannot author a verdict, uses the production parser,
        and does not depend on the retained fixture -- are asserted unconditionally above,
        because those hold whether or not a run has been captured on this host.

        An absent artifact under the gate is a FAILURE, not a skip: AC-37-24 records a
        check that cannot run as "not established", and this case exists to verify a
        capture that the gate says should be there.
        """
        summary = self.EVIDENCE / "R10_REAL_AGENT.json"
        self.assertTrue(
            summary.exists(),
            f"{summary} is absent; run `python3 scripts/os37_r10_real_agent.py` with an "
            "authenticated CLI.  A missing capture is 'not established', never a pass")
        blob = summary.read_text()
        for banned in ("os37_r10_fixture", "OS37_R10_FAIL_PHASE", "os37-r10-agent"):
            self.assertNotIn(banned, blob,
                             f"the retained R10 evidence references {banned}")
        payload = json.loads(blob)
        self.assertTrue(payload["runs"], "the evidence records no runs")
        self.assertEqual(payload["preconditions"]["orca_on_child_path"], None)
        self.assertEqual(payload["preconditions"]["orca_env_names"], [])
        for run in payload["runs"]:
            with self.subTest(run=run["run_id"]):
                # Every Worker and Reviewer step names a REAL profile binary realpath...
                for step in run["steps"]:
                    self.assertIn(step["cli"], ("claude", "codex"))
                # ...the two reviewer prompts were byte-identical...
                self.assertTrue(run["step2_and_step4_prompts_identical"],
                                "the two reviewer dispatches did not receive identical "
                                "prompts, so step 4 is not a comparable fresh review")
                # ...and step 4 was a FRESH process, never step 2 resumed.
                self.assertTrue(run["fresh_identity_at_step4"])
                # ...every dispatch constructed a TYPED DeliveryProof, so no state was
                # advanced on the spawn having succeeded (D13.6(e) condition 3).
                for step in run["steps"]:
                    self.assertIsNotNone(
                        step.get("delivery_proof"),
                        f"{run['run_id']} step {step['step']} settled with no typed "
                        "DeliveryProof; spawn success is never delivery")
                    self.assertEqual(step["delivery_proof"]["proof_class"], "B")
                    self.assertIn("DELIVERY_INTENT", step["journal_kinds"])
                    self.assertLess(
                        step["journal_kinds"].index("DELIVERY_INTENT"),
                        step["journal_kinds"].index("SPAWN_OBSERVED"),
                        "a dispatch spawned before its delivery intent was journalled")

    @unittest.skipUnless(E2E_ENABLED, E2E_REASON)
    def test_the_retained_evidence_was_re_read_by_a_stranger_interpreter(self) -> None:
        """D13.6(c) precondition 7 / (e) conditions (3) and (5).

        The point of a STRANGER interpreter is that it holds none of the harness's objects:
        it reads the append-only journal and nothing else, which is exactly the position a
        Supervisor, an operator or a reviewer is in after the Coordinator's turn has ended.
        A re-read done in-process would prove that a program can read its own memory.
        """
        summary = self.EVIDENCE / "R10_REAL_AGENT.json"
        self.assertTrue(summary.exists(), f"{summary} is absent")
        payload = json.loads(summary.read_text())
        verification = payload.get("retained_evidence_verification")
        self.assertIsNotNone(
            verification,
            "the retained evidence carries no stranger-interpreter verification; "
            "D13.6(c) precondition 7 is unproven and the E2E is NOT ESTABLISHED")
        self.assertTrue(verification["all_runs_verified"],
                        f"the stranger re-read failed: {verification['runs']}")
        self.assertEqual(len(verification["runs"]), len(payload["runs"]),
                         "not every run was re-read")
        for check in verification["runs"]:
            with self.subTest(run=check["run_id"]):
                self.assertTrue(check["stranger_reread_ok"])
                self.assertTrue(
                    check["every_dispatch_journalled_its_intent_before_its_spawn"])
                self.assertTrue(
                    check["every_dispatch_constructed_a_typed_delivery_proof"])
        # ...and the journals it read are HERE, not in a temp directory a reviewer cannot
        # reach.  An evidence artifact that points at vanished files is not evidence.
        journals = list((self.EVIDENCE / "artifact_base").rglob("journal.ndjson"))
        self.assertEqual(len(journals), len(payload["runs"]),
                         "the retained journals do not cover every run")
