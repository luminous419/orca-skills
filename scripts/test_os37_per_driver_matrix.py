"""OS-37 R1 -- the PER-DRIVER lifecycle matrix: Claude AND Codex, six outcomes each.

Why this module exists, plainly.  The first TEST iteration mapped R1 onto a twelve-case
"matrix" whose helper *returned the answer*: ``delivered_confirmed``, ``INTERRUPTED`` and
``TIMED_OUT`` were literals, and completion/failure/lost only called
``completion_evidence`` with a caller-chosen exit code and empty output.  The assertion
that followed -- that those strings belong to a closed vocabulary -- is true of the
literals themselves and says nothing about either driver.  The review named that
(**F-003**, G5), and it was right: a coverage claim has to be discharged by the code under
test producing the value, not by the test naming it.

So every case below **drives the value out of production code** and asserts what came
back:

* :class:`PerDriverEvidenceBoundaryTests` -- deterministic, ungated.  Real pty pairs, real
  ``drivers.deliver`` writes, real exit sentinels written by ``write_exit_sentinel`` and
  read back by ``read_exit_sentinel``, the real interrupt ladder, and the real readiness
  decision.  Nothing is stubbed except the *process table* -- the one seam the repository
  already uses for the recycled-pid and stale-snapshot refusals, which cannot be produced
  by a real process on demand.
* :class:`LivePerDriverOutcomeMatrixTests` -- ``ORCA_OS37_E2E=1``.  Seven cases x two
  drivers = FOURTEEN real local processes on fourteen real headless PTYs, driven through
  :class:`StandaloneSession`, which is the same object ``StandaloneAdapter.start`` drives.
  The *session* computes each verdict; the test only reads it.

Both halves are parameterised over ``("claude", "codex")`` from ONE body, and each case
asserts the observed evidence carries **that driver's own record type** -- so a driver that
silently answered with the other's vocabulary would fail rather than pass.

**What this module does NOT establish**, stated here rather than left to inference: it says
nothing about the REAL installed CLIs.  Every case here runs against the native stub fixture
in a mode that emits the driver's record shape, so the claim is a RUNTIME-BOUNDARY claim for
both drivers and no more.  The real-CLI half lives in
``test_os37_cli_preconditions.LiveCliTests`` and in ``evidence/r10_real_agent/``, and the two
verdicts are deliberately kept in separate columns (D-H).

Both drivers' profiles here declare ``identity_binding="minted_echo"``, which is the fixture's
honest binding: the stub echoes the id it is given.  The real Codex CLI mints its own and is
bound in ``adopted`` mode instead (D4.4 A-1..A-6) -- a distinction this module does not
exercise and does not claim to.
"""
from __future__ import annotations

import json
import os
import pty
import tempfile
import time
import unittest
from pathlib import Path

from scripts import os37_native_stub as native_stub
from scripts.deterministic_workflow import standalone_drivers as drivers
from scripts.deterministic_workflow import standalone_identity as identity
from scripts.deterministic_workflow import standalone_interrupt as interrupt_mod
from scripts.deterministic_workflow import standalone_lifecycle as lifecycle
from scripts.deterministic_workflow import standalone_pty as pty_supervisor
from scripts.deterministic_workflow.standalone_profile import (CompletionSelector,
                                                            DeliveryProofSelector,
                                                            ReadinessSelector,
                                                                StandaloneProfile,
                                                                Timeouts)

REPO = Path(__file__).resolve().parent.parent

DRIVER_NAMES = ("claude", "codex")

#: The six outcomes R1 names, in the order the requirement lists them.
OUTCOMES = ("delivery", "completion", "failure", "interruption", "timeout", "lost")

#: Each driver's OWN record vocabulary, as the driver's methods select it.  Kept here as
#: DATA so one parameterised body can assert "the evidence is this driver's", and so a
#: driver answering with the other's record type fails instead of passing.
DRIVER_SHAPES = {
    "claude": {
        "turn_start": {"type": "message_start"},
        "completion": {"type": "result", "subtype": "success"},
        "readiness_record_type": "system",
        "readiness_session_field": "session_id",
        "stub_deliver_mode": "deliver-claude",
        "stub_complete_mode": "complete-claude",
    },
    "codex": {
        "turn_start": {"type": "item.started", "item": {"id": "item_0"}},
        "completion": {"type": "turn.completed", "usage": {"input_tokens": 1}},
        "readiness_record_type": "thread.started",
        "readiness_session_field": "thread_id",
        "stub_deliver_mode": "deliver-codex",
        "stub_complete_mode": "complete-codex",
    },
}

STUB_BIN = native_stub.native_stub_dir()

E2E_REASON = "requires ORCA_OS37_E2E=1; the standalone E2E drives real local processes"
E2E_ENABLED = os.environ.get("ORCA_OS37_E2E") == "1"

TTY = "ttys777"
CHILD_PID = 7777


def driver_profile(name: str, **overrides) -> StandaloneProfile:
    """A profile for ``name`` whose readiness selector is THAT CLI's record shape."""
    shape = DRIVER_SHAPES[name]
    fields = dict(
        driver=name, binary=name,
        supported_range=((0, 0, 0), (99, 0, 0)),
        readiness_records=(ReadinessSelector(
            channel="structured", record_type=shape["readiness_record_type"],
            session_field=shape["readiness_session_field"]),),
        # The twelve runtime-boundary cases run against a fixture CLI that WAITS, so the
        # honest declaration is `post_ready_delivery` -- which is also what keeps that
        # mode's `ReadyToken` gate and its framing steps exercised on every run.  D-H:
        # this boundary half is INADMISSIBLE as evidence for the real-CLI half, and the
        # matrix carries two separate verdict columns for exactly that reason.
        delivery_mode="post_ready_delivery",
        identity_binding="minted_echo", identity_flag="--session-id",
        delivery_proofs=(DeliveryProofSelector(
            channel="structured", record_type=shape["turn_start"]["type"]),),
        completion_records=(CompletionSelector(
            channel="structured", record_type=shape["completion"]["type"]),),
        timeouts=Timeouts(graceful_force_timeout_ms=20, force_retry_ms=1,
                          physical_exit_timeout_ms=20, staleness_budget_ms=1000,
                          delivery_verify_timeout_ms=3000))
    fields.update(overrides)
    return StandaloneProfile(**fields)


def _record(**overrides) -> dict:
    fields = dict(
        run_id="run_matrix", repo_id="repo-1",
        worktree_selector=identity.stable_worktree_selector("repo-1", "/tmp/wt"),
        agent_id="agent-1", task_id="task-1", dispatch_id="dispatch-1",
        session_id="s-matrix", pid=CHILD_PID, pgid=CHILD_PID, sid=CHILD_PID,
        captured_tty=TTY, pty_id="pty-1", process_incarnation="i-1",
        host_scope="local", spawn_token="t-1", started_at="2026-09-10T00:00:00Z",
        argv_digest="ad", env_digest="ed", created_by_this_runtime=True,
        resource_kind="pty_session", user_taken_over=False)
    fields.update(overrides)
    return identity.make_record(**fields)


def _snapshot(rows=None, *, readable: bool = True) -> dict:
    if rows is None:
        rows = ({"pid": CHILD_PID, "ppid": 1, "pgid": CHILD_PID, "sid": CHILD_PID,
                 "tty": TTY, "stat": "Ss"},)
    return {"tty": TTY, "captured_at": time.time(), "rows": tuple(rows),
            "readable": readable}


# =====================================================================================
class PerDriverEvidenceBoundaryTests(unittest.TestCase):
    """Six outcomes x two drivers, each produced by PRODUCTION code from that CLI's bytes.

    The inputs are per-driver: the transcript text is the record shape that driver's own
    parser selects, the exit status comes from a sentinel this module really wrote and
    ``read_exit_sentinel`` really read back, and the delivery write really crosses a pty.
    The outputs are read, never asserted into existence.
    """

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())

    # -- 1. DELIVERY --------------------------------------------------------------------
    def test_delivery_is_confirmed_through_each_drivers_own_turn_start_proof(self) -> None:
        """``drivers.deliver`` over a REAL pty, verified by the driver's OWN parser.

        The frame really crosses ``master -> slave``; the "agent" side really answers with
        that driver's turn-start record; and the proof comes back from
        ``driver.turn_start_evidence`` rather than from this test.  The frame echo is
        deliberately NOT available as a shortcut -- the pty is put in raw mode with echo
        off -- so ``delivered_confirmed`` here can only have been reached through the
        driver's structured proof.
        """
        for name in DRIVER_NAMES:
            with self.subTest(driver=name):
                profile = driver_profile(name)
                driver = drivers.driver_for(profile)
                master_fd, slave_fd = pty.openpty()
                self.addCleanup(os.close, master_fd)
                self.addCleanup(os.close, slave_fd)
                _disable_echo(slave_fd)
                seen: dict[str, object] = {}

                def verify(_working: bool, _driver=driver, _slave=slave_fd,
                           _master=master_fd, _seen=seen, _name=name) -> dict:
                    # The "agent" side: read the frame this runtime just wrote, then answer
                    # with THIS driver's turn-start record.
                    _seen["frame"] = _read_available(_slave)
                    os.write(_slave,
                             (json.dumps(DRIVER_SHAPES[_name]["turn_start"]) + "\r\n")
                             .encode())
                    text = _read_available(_master, budget_s=2.0)
                    _seen["evidence"] = _driver.turn_start_evidence(text)
                    if _seen["evidence"] is not None:
                        return {"delivery": "delivered_confirmed", "proof": "turn_start"}
                    return {"delivery": "not_observed", "proof": None}

                result = drivers.deliver(
                    master_fd, "PAYLOAD-FOR-" + name.upper(), profile=profile,
                    measured_ingest_rate=1_000_000.0, verify=verify,
                    sleep=lambda _s: None)

                self.assertIn(result["delivery"], lifecycle.DELIVERY_OUTCOMES)
                self.assertEqual(
                    result["delivery"], "delivered_confirmed",
                    f"{name}: delivery was not confirmed through the driver's own proof; "
                    f"the frame observed on the slave was {seen.get('frame')!r}")
                self.assertEqual(result["proof"], "turn_start")
                self.assertIn(result["proof"], lifecycle.DELIVERY_PROOFS)
                self.assertGreater(result["frame_bytes"], 0)

                # The bytes really crossed the pty, framed as D4.3 specifies.
                frame = str(seen.get("frame", ""))
                self.assertIn("PAYLOAD-FOR-" + name.upper(), frame)
                self.assertIn("\x1b[200~", frame)
                self.assertIn("\x1b[201~", frame)

                # And the proof is THIS driver's, not the other's.
                evidence = seen["evidence"]
                self.assertIsNotNone(evidence)
                self.assertEqual(evidence["kind"], "turn_start")           # type: ignore[index]
                self.assertEqual(evidence["source_vocabulary"]["driver"],  # type: ignore[index]
                                 name)
                self.assertEqual(
                    evidence["source_vocabulary"]["type"],                 # type: ignore[index]
                    DRIVER_SHAPES[name]["turn_start"]["type"])

    def test_neither_driver_accepts_the_other_drivers_turn_start_record(self) -> None:
        """The matrix would be vacuous if both drivers answered to the same bytes."""
        for name in DRIVER_NAMES:
            other = "codex" if name == "claude" else "claude"
            with self.subTest(driver=name, foreign_record=other):
                driver = drivers.driver_for(driver_profile(name))
                text = json.dumps(DRIVER_SHAPES[other]["turn_start"]) + "\n"
                self.assertIsNone(
                    driver.turn_start_evidence(text),
                    f"the {name} driver accepted the {other} CLI's turn-start record; the "
                    "per-driver matrix would then prove nothing per driver")

    # -- 2. COMPLETION and 3. FAILURE ---------------------------------------------------
    def test_completion_and_failure_come_from_a_real_sentinel_and_the_drivers_record(
            self) -> None:
        """A REAL fenced exit sentinel plus THAT driver's completion record.

        ``write_exit_sentinel`` is the production writer the pty session leader calls and
        ``read_exit_sentinel`` is the production reader a stranger process calls; the exit
        status therefore travels the same path it does in a real run.  The verdict comes
        from ``lifecycle.map_exit_code`` over the profile's own table.
        """
        for name in DRIVER_NAMES:
            for case, code, table, expected in (
                    ("completion", 0, {0: "COMPLETED"}, "COMPLETED"),
                    ("failure", 2, {0: "COMPLETED", 2: "FAILED"}, "FAILED")):
                with self.subTest(driver=name, case=case):
                    profile = driver_profile(name, exit_code_map=table)
                    driver = drivers.driver_for(profile)
                    fence = f"s-{name}-{case}:i-1"
                    path = self.base / f"exit.{name}.{case}"
                    pty_supervisor.write_exit_sentinel(path, code=code, fence=fence)
                    sentinel = pty_supervisor.read_exit_sentinel(path, fence=fence)
                    self.assertEqual(sentinel["outcome"], "exited")

                    text = json.dumps(DRIVER_SHAPES[name]["completion"]) + "\n"
                    evidence = driver.completion_evidence(
                        text, exit_status=sentinel["code"],
                        exit_proven=sentinel["outcome"] == "exited")

                    # Both completion gates, read off the evidence rather than assumed.
                    self.assertTrue(evidence["exit_proven"])
                    self.assertEqual(evidence["exit_status"], code)
                    self.assertIsNotNone(
                        evidence["settlement_record"],
                        f"the {name} driver did not recognise its own completion record")
                    self.assertEqual(evidence["settlement_record"]["type"],
                                     DRIVER_SHAPES[name]["completion"]["type"])
                    self.assertEqual(evidence["lost_reason"], "")
                    self.assertEqual(evidence["source_vocabulary"]["driver"], name)

                    mapped = lifecycle.map_exit_code(evidence["exit_status"],
                                                     profile.exit_code_map)
                    self.assertIn(mapped["state"], lifecycle.STATES)
                    self.assertEqual(mapped["state"], expected)

    def test_a_driver_does_not_recognise_the_other_drivers_completion_record(self) -> None:
        for name in DRIVER_NAMES:
            other = "codex" if name == "claude" else "claude"
            with self.subTest(driver=name, foreign_record=other):
                driver = drivers.driver_for(driver_profile(name))
                text = json.dumps(DRIVER_SHAPES[other]["completion"]) + "\n"
                self.assertIsNone(
                    driver.completion_record(text),
                    f"the {name} driver settled on the {other} CLI's completion record")

    # -- 4. INTERRUPTION ----------------------------------------------------------------
    def test_interruption_runs_the_real_ladder_for_each_driver(self) -> None:
        """The real ladder, per driver, with the process table as the only injected seam.

        A live process cannot be made to disappear between two named rungs on demand, which
        is why the repository already drives this path over an injected table.  Everything
        else -- the gates, the signal decisions, the outcome, the lifecycle mapping -- is
        production code, and the live half below repeats it against a real child.
        """
        for name in DRIVER_NAMES:
            with self.subTest(driver=name):
                sent: list[tuple[int, int]] = []
                calls = {"n": 0}

                def reader(_tty, _calls=calls):
                    _calls["n"] += 1
                    return _snapshot() if _calls["n"] <= 1 else _snapshot(rows=())

                result = interrupt_mod.interrupt(
                    "intent-matrix", "operator asked", record=_record(),
                    profile=driver_profile(name), table_reader=reader,
                    supervisor_pid=999,
                    killpg=lambda pgid, sig: sent.append((pgid, sig)),
                    kill=lambda pid, sig: sent.append((pid, sig)),
                    sleep=lambda _s: None)

                self.assertIn(result["interrupt_outcome"],
                              lifecycle.INTERRUPT_OUTCOMES)
                self.assertEqual(result["interrupt_outcome"], "interrupted_confirmed")
                mapped = interrupt_mod.lifecycle_for(result["interrupt_outcome"])
                self.assertEqual(mapped["state"], "INTERRUPTED")
                self.assertIn(mapped["state"], lifecycle.STATES)
                self.assertEqual(mapped["lost_reason"], "")
                self.assertEqual([sig for _t, sig in sent], [15],
                                 f"{name}: the ladder escalated past a proven exit")
                gates = [step for step in result["ladder"]
                         if step["rung"].startswith("G")]
                self.assertTrue(gates, f"{name}: the ladder recorded no identity gate")
                for step in gates:
                    self.assertTrue(step["identity_verified"],
                                    f"{name}: gate {step['rung']} verified no identity")

    # -- 5. TIMEOUT ---------------------------------------------------------------------
    def test_timeout_is_the_readiness_quorum_refusing_this_drivers_own_frames(self) -> None:
        """A live process, a well-formed record OF THE DECLARED TYPE, a FOREIGN identity.

        R-B is equality against a locally minted value, so this is the case that separates
        "the CLI said something" from "the CLI said OUR thing".  The evidence is built by
        the driver from that driver's record shape, and the verdict by
        ``lifecycle.decide_readiness`` -- which takes no ``supplementary`` parameter at all.
        """
        for name in DRIVER_NAMES:
            with self.subTest(driver=name):
                shape = DRIVER_SHAPES[name]
                profile = driver_profile(name)
                driver = drivers.driver_for(profile)
                minted = identity.mint_session_id(run_id="r", dispatch_id="d", task_id="t")
                foreign = {"type": shape["readiness_record_type"],
                           shape["readiness_session_field"]: "somebody-elses-id"}
                text = json.dumps(foreign) + "\n"
                liveness = {"r_a": True, "legs": {"waitpid": True, "table": True,
                                                  "tty": True, "image": True},
                            "satisfied": True}
                evidence = driver.readiness_evidence(
                    text, minted_session_id=minted, liveness=liveness)
                self.assertIsNone(
                    evidence["bound_signal"],
                    f"{name}: a foreign identity satisfied R-B")

                verdict = lifecycle.decide_readiness(
                    evidence["liveness"], evidence["bound_signal"], evidence["refusals"],
                    minted_session_id=minted,
                    declared_record_types=(shape["readiness_record_type"],))
                self.assertNotEqual(verdict["verdict"], "ready")
                self.assertFalse(verdict["quorum"]["R-B"])

                # And the runtime's own resolution of a readiness deadline is TIMED_OUT
                # with the delivery left UNKNOWN -- never "not ready" as a fact.
                resolved = lifecycle.resolve_unknown("readiness_timeout")
                self.assertEqual(resolved["state"], "TIMED_OUT")
                self.assertIn(resolved["state"], lifecycle.STATES)

    # -- 6. LOST ------------------------------------------------------------------------
    def test_lost_is_what_an_absent_sentinel_produces_for_each_driver(self) -> None:
        """No sentinel -> no proven exit -> LOST with a NAMED reason, per driver.

        The sentinel is genuinely absent: nothing wrote one, and ``read_exit_sentinel``
        says ``absent`` rather than the test saying it.  This is DESIGN risk DR-2's
        fail-closed branch -- the pty session leader was killed before it could write.
        """
        for name in DRIVER_NAMES:
            with self.subTest(driver=name):
                profile = driver_profile(name)
                driver = drivers.driver_for(profile)
                fence = f"s-{name}-lost:i-1"
                path = self.base / f"exit.{name}.never-written"
                sentinel = pty_supervisor.read_exit_sentinel(path, fence=fence)
                self.assertEqual(sentinel["outcome"], "absent")

                evidence = driver.completion_evidence(
                    "", exit_status=sentinel["code"],
                    exit_proven=sentinel["outcome"] == "exited")
                self.assertFalse(evidence["exit_proven"])
                self.assertIn(evidence["lost_reason"], lifecycle.LOST_REASONS)
                self.assertEqual(evidence["lost_reason"], "cause_unreported")
                self.assertEqual(evidence["source_vocabulary"]["driver"], name)

                resolved = lifecycle.resolve_unknown("exit_status_absent")
                self.assertEqual(resolved["state"], "LOST")
                self.assertIn(resolved["lost_reason"], lifecycle.LOST_REASONS)

                # And an exit that IS proven but carries an unmapped code is LOST too --
                # never `exited{0}`, which is the reduction G-7 being UNKNOWN forbids.
                unmapped = lifecycle.map_exit_code(3, profile.exit_code_map)
                self.assertEqual(unmapped["state"], "LOST")
                self.assertEqual(unmapped["lost_reason"], "exit_code_unmapped")

    # -- the matrix is COMPLETE, and that is asserted rather than eyeballed --------------
    def test_every_one_of_the_six_outcomes_has_a_case_for_both_drivers(self) -> None:
        """A coverage claim the file itself has to keep true.

        Each of the six outcomes must be named by at least one test method in BOTH classes
        -- the deterministic half and the live half -- and every case in both must really
        iterate ``DRIVER_NAMES`` rather than hard-code one driver.  A future edit that drops
        a case, or quietly halves the matrix to a single driver, fails here instead of
        shrinking R1 in silence.
        """
        stems = {"delivery": "deliver", "completion": "completion", "failure": "fail",
                 "interruption": "interrupt", "timeout": "timeout", "lost": "lost"}
        self.assertEqual(set(stems), set(OUTCOMES), "the stem table drifted from OUTCOMES")
        for label, klass in (("deterministic", PerDriverEvidenceBoundaryTests),
                             ("live", LivePerDriverOutcomeMatrixTests)):
            body = "\n".join(attr for attr in dir(klass) if attr.startswith("test_"))
            missing = [outcome for outcome, stem in stems.items() if stem not in body]
            self.assertEqual(
                missing, [],
                f"R1 outcomes with no {label} case: {missing}; the matrix must not shrink")
        self.assertEqual(set(DRIVER_NAMES), set(DRIVER_SHAPES),
                         "a driver was added to the matrix without a record vocabulary")
        # And every case really is parameterised over BOTH drivers rather than hard-coding
        # one.  Read off each method's own source, so a case that quietly dropped a driver
        # fails here instead of halving the matrix in silence.
        import inspect
        unparameterised = []
        for klass in (PerDriverEvidenceBoundaryTests, LivePerDriverOutcomeMatrixTests):
            for attr in dir(klass):
                if not attr.startswith("test_") or attr == self._testMethodName:
                    continue
                if "for name in DRIVER_NAMES:" not in inspect.getsource(
                        getattr(klass, attr)):
                    unparameterised.append(f"{klass.__name__}.{attr}")
        self.assertEqual(
            unparameterised, [],
            "these cases do not iterate DRIVER_NAMES, so the per-driver claim they carry "
            f"is partial: {unparameterised}")


def _disable_echo(fd: int) -> None:
    import termios
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON)
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def _read_available(fd: int, *, budget_s: float = 1.0) -> str:
    import select
    out = b""
    deadline = time.time() + budget_s
    while time.time() < deadline:
        ready, _w, _x = select.select([fd], [], [], 0.05)
        if not ready:
            if out:
                break
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    return out.decode("utf-8", "replace")


# =====================================================================================
@unittest.skipUnless(E2E_ENABLED, E2E_REASON)
class LivePerDriverOutcomeMatrixTests(unittest.TestCase):
    """The same six outcomes x two drivers, against REAL local processes on REAL PTYs.

    Driven through :class:`StandaloneSession` -- the object ``StandaloneAdapter.start``
    drives -- so the verdict is computed by the runtime and merely read here.  The agent is
    the native stub fixture in a mode that emits THAT driver's record shape, so the
    per-driver claim is per-driver at the only layer where the two CLIs differ.

    Both drivers' sessions reach READY here -- ``_live_session`` asserts
    ``start_outcome == "ready"`` for each -- because the fixture echoes the minted id and the
    profiles declare ``minted_echo`` accordingly.  That is a statement about the FIXTURE, not
    about either installed CLI: the real Codex CLI mints its own ``thread_id`` and binds in
    ``adopted`` mode, which is covered by ``test_os37_cli_preconditions`` against the real
    binary rather than here.
    """

    def setUp(self) -> None:
        if STUB_BIN is None:
            self.skipTest(native_stub.NO_COMPILER_REASON)
        self.base = Path(tempfile.mkdtemp())
        os.environ.setdefault("OS37_E2E_KEY_SOURCE", "not-a-real-key")
        self._sessions: list = []

    def tearDown(self) -> None:
        for session in self._sessions:
            _reap(session)

    def _live_session(self, name: str, mode: str, *, run: str,
                      exit_code: str | None = None, **profile_overrides):
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession

        env = {"OS37_STUB_MODE": mode}
        if exit_code is not None:
            env["OS37_STUB_EXIT_CODE"] = exit_code
        profile = driver_profile(
            name, binary="os37-stub-cli", bin_dirs=(str(STUB_BIN),),
            supported_range=((1, 0, 0), (2, 0, 0)),
            driver_env=env,
            auth_secret_ref={"ANTHROPIC_API_KEY": "OS37_E2E_KEY_SOURCE"},
            # `completion_timeout_ms` is DECLARED here, and it has to be: OS-37's external
            # review #4 gave completion its own bound instead of borrowing the readiness
            # one, and its production default is sized for a real agent turn (30 minutes).
            # This matrix deliberately drives cases that never close both completion gates
            # -- the timeout case and the unclassifiable-exit case -- so a fixture that did
            # not declare a bound would sit in `await_completion` for half an hour each.
            # A fixture's deadline belongs to the fixture; the default belongs to a real
            # agent.
            timeouts=Timeouts(preflight_timeout_ms=4000, readiness_timeout_ms=6000,
                              delivery_verify_timeout_ms=6000,
                              completion_timeout_ms=8000,
                              graceful_force_timeout_ms=1500,
                              physical_exit_timeout_ms=3000, force_retry_ms=100),
            **profile_overrides)
        intent = {"intent_id": f"intent-{run}", "command_id": "c", "payload_digest": "d",
                  "run_id": run, "phase": "IMPLEMENTATION", "role": "WORKER",
                  "round_kind": "PHASE_GATE"}
        ledger = InMemoryRuntimeStateStore()
        claim = ledger.claim(intent)
        session = StandaloneSession(
            intent=intent, profile=profile, artifact_base=self.base, run_id=run,
            journal=sj.ExecutionJournal(self.base, run), runtime_state=ledger)
        self._sessions.append(session)
        shape = DRIVER_SHAPES[name]
        receipt = session.start(
            lease_token=claim["lease_token"],
            help_text=("--bare --settings --session-id -p --output-format stream-json "
                       "exec --json --ephemeral --skip-git-repo-check --color"),
            prober=_version_prober,
            # The rehearsal is a SEPARATE bounded spawn; it is injected here for both
            # drivers so the twelve cases below measure the outcome under test and not the
            # rehearsal.  The rehearsal's own real behaviour is covered by
            # `test_os37_cli_preconditions.ReadinessRehearsalTests` and, live, by
            # `test_os37_standalone_e2e.LiveReadinessQuorumTests`.
            rehearsal=lambda _p, _e, s, _t=shape: {
                "channel": "structured", "record_type": _t["readiness_record_type"],
                _t["readiness_session_field"]: s, "session_id": s},
            # The DELIVERY-MODE rehearsal is injected for the same reason and with the same
            # scope: it is a SEPARATE bounded spawn, and these twelve cases measure the
            # OUTCOME under test rather than the rehearsal.  The rehearsal's own real
            # behaviour -- including all four of D4.2b's fail-closed outcomes and the W-2
            # auth-marker override -- is covered un-injected by
            # `test_os37_cli_preconditions.DeliveryModeRehearsalTests`, three of whose cases
            # drive REAL spawns of the `os37-waiting-cli` and `os37-mode-liar-cli` fixtures.
            mode_rehearsal=lambda _p, _e: {
                "r_b_closed": True, "delivery_proof": True, "auth_marker": None,
                # The fixture CLI genuinely reaches the quorum and then WAITS, which is what
                # makes `post_ready_delivery` its honest declaration.
                "waited_without_prompt": True, "evaluable": True,
                "identity_bound": True, "detail": {"injected": "outcome-matrix case"}})
        self.assertEqual(receipt["start_outcome"], "ready", receipt["failure_reason"])
        return session

    # -- 1. DELIVERY --------------------------------------------------------------------
    def test_live_delivery_is_confirmed_for_both_drivers(self) -> None:
        for name in DRIVER_NAMES:
            with self.subTest(driver=name):
                session = self._live_session(
                    name, DRIVER_SHAPES[name]["stub_deliver_mode"],
                    run=f"run_live_deliver_{name}")
                result = session.send({"payload": f"a real prompt for {name}"})
                self.assertIn(result["delivery"], lifecycle.DELIVERY_OUTCOMES)
                self.assertEqual(
                    result["delivery"], "delivered_confirmed",
                    f"{name}: {session.capture.text()[-400:]!r}")
                self.assertIn(result["proof"], lifecycle.DELIVERY_PROOFS)
                self.assertEqual(session.state, "PROMPT_DELIVERED")
                self.assertIn("delivery_proof_observed", session.event_log)
                self.assertIn(DRIVER_SHAPES[name]["turn_start"]["type"],
                              session.capture.text(),
                              f"{name}: the child never emitted its own turn-start record")

    # -- 2. COMPLETION ------------------------------------------------------------------
    def test_live_completion_needs_both_gates_for_both_drivers(self) -> None:
        for name in DRIVER_NAMES:
            with self.subTest(driver=name):
                session = self._live_session(
                    name, DRIVER_SHAPES[name]["stub_complete_mode"],
                    run=f"run_live_complete_{name}", exit_code="0",
                    exit_code_map={0: "COMPLETED"})
                outcome = session.await_completion()
                self.assertIn(outcome["state"], lifecycle.STATES)
                self.assertEqual(outcome["state"], "COMPLETED",
                                 f"{name}: {outcome['evidence']}")
                evidence = outcome["evidence"]
                self.assertTrue(evidence["exit_proven"], "the exit sentinel gate is open")
                self.assertEqual(evidence["exit_status"], 0)
                self.assertEqual(evidence["settlement_record"]["type"],
                                 DRIVER_SHAPES[name]["completion"]["type"])
                self.assertEqual(evidence["source_vocabulary"]["driver"], name)
                self.assertEqual(outcome["lost_reason"], "")

    # -- 3. FAILURE ---------------------------------------------------------------------
    def test_live_failure_comes_from_a_real_nonzero_exit_for_both_drivers(self) -> None:
        """A real non-zero ``waitpid`` status, mapped by the profile's own table.

        The child emits NO completion record, so the only thing left is its exit status --
        which is exactly the branch ``await_completion`` routes through ``map_exit_code``.
        """
        for name in DRIVER_NAMES:
            with self.subTest(driver=name):
                session = self._live_session(
                    name, "exit-code", run=f"run_live_fail_{name}", exit_code="2",
                    exit_code_map={0: "COMPLETED", 2: "FAILED"})
                outcome = session.await_completion()
                self.assertIn(outcome["state"], lifecycle.STATES)
                self.assertEqual(outcome["state"], "FAILED",
                                 f"{name}: {outcome['evidence']}")
                self.assertTrue(outcome["evidence"]["exit_proven"])
                self.assertEqual(outcome["evidence"]["exit_status"], 2)
                self.assertIsNone(outcome["evidence"]["settlement_record"])

    # -- 4. INTERRUPTION ----------------------------------------------------------------
    def test_live_interruption_is_proven_for_both_drivers(self) -> None:
        for name in DRIVER_NAMES:
            with self.subTest(driver=name):
                session = self._live_session(name, "alive",
                                             run=f"run_live_int_{name}")
                result = session.interrupt("operator asked")
                self.assertIn(result["interrupt_outcome"],
                              lifecycle.INTERRUPT_OUTCOMES)
                self.assertIn(
                    result["interrupt_outcome"],
                    ("interrupted_confirmed", "terminated_forced"),
                    f"{name}: the ladder proved no exit: {result['interrupt_outcome']}")
                self.assertEqual(session.state, "INTERRUPTED")
                self.assertEqual(session.lost_reason, "")
                gates = [step for step in result["ladder"]
                         if step["rung"].startswith("G")]
                self.assertTrue(gates, f"{name}: no identity gate ran")
                for step in gates:
                    self.assertTrue(step["identity_verified"],
                                    f"{name}: gate {step['rung']} verified no identity")

    # -- 5. TIMEOUT ---------------------------------------------------------------------
    def test_live_timeout_for_both_drivers_is_timed_out_not_a_negative_fact(self) -> None:
        for name in DRIVER_NAMES:
            with self.subTest(driver=name):
                session = self._live_session(name, "no-readiness",
                                             run=f"run_live_timeout_{name}")
                outcome = session.await_ready()
                self.assertIn(outcome["state"], lifecycle.STATES)
                self.assertEqual(outcome["state"], "TIMED_OUT")
                self.assertEqual(outcome["verdict"]["verdict"], "unprovable")
                self.assertEqual(session.state, "TIMED_OUT")
                self.assertNotIn("readiness_observed", session.event_log)

    # -- 6. LOST ------------------------------------------------------------------------
    def test_live_lost_for_both_drivers_when_the_exit_cannot_be_classified(self) -> None:
        """A REAL child, a REAL exit status, and an EMPTY exit-code table.

        G-7 is UNKNOWN -- no per-cause exit-code table has been measured for either CLI --
        and the fail-closed consequence is asserted here against a live process: an exit
        this runtime cannot classify is ``LOST`` with ``exit_code_unmapped``, never
        ``exited{0}`` and never a completion inferred from the process merely having ended.
        """
        for name in DRIVER_NAMES:
            with self.subTest(driver=name):
                session = self._live_session(
                    name, "exit-code", run=f"run_live_lost_{name}", exit_code="3",
                    exit_code_map={})
                outcome = session.await_completion()
                self.assertIn(outcome["state"], lifecycle.STATES)
                self.assertEqual(outcome["state"], "LOST", f"{name}: {outcome}")
                self.assertEqual(outcome["lost_reason"], "exit_code_unmapped")
                self.assertIn(outcome["lost_reason"], lifecycle.LOST_REASONS)
                self.assertTrue(outcome["evidence"]["exit_proven"])
                self.assertEqual(outcome["evidence"]["exit_status"], 3)
                self.assertIsNone(outcome["evidence"]["settlement_record"])
                self.assertEqual(outcome["evidence"]["source_vocabulary"]["driver"], name)

    def test_live_a_sigkilled_process_group_is_lost_and_never_completed(self) -> None:
        """DR-2's residual, produced for real, asserted for what it GUARANTEES.

        ``SIGKILL`` to the whole group races the session leader's own ``waitpid``: sometimes
        the leader reaps the agent and writes the fenced sentinel (status ``128+9``) before
        it dies, sometimes it does not.  Both are real, and the design's obligation is the
        same in both -- a NAMED ``LOST``, never ``COMPLETED`` and never ``FAILED``.  So that
        is what is asserted, rather than a race outcome that would make the test flaky.
        """
        import signal as _signal
        for name in DRIVER_NAMES:
            with self.subTest(driver=name):
                session = self._live_session(name, "alive",
                                             run=f"run_live_killed_{name}")
                os.killpg(session.pty["pgid"], _signal.SIGKILL)
                for _ in range(60):
                    try:
                        done, _status = os.waitpid(session.pty["pid"], os.WNOHANG)
                    except OSError:
                        break
                    if done:
                        break
                    time.sleep(0.05)
                outcome = session.await_completion()
                self.assertIn(outcome["state"], lifecycle.STATES)
                self.assertEqual(outcome["state"], "LOST", f"{name}: {outcome}")
                self.assertIn(outcome["lost_reason"], lifecycle.LOST_REASONS)
                self.assertNotEqual(outcome["lost_reason"], "",
                                    f"{name}: LOST was reported with no named reason")
                self.assertIsNone(
                    outcome["evidence"]["settlement_record"],
                    f"{name}: a killed process produced a settlement record")


def _version_prober(argv, env, *, timeout_ms, cwd=None):
    from scripts.deterministic_workflow import standalone_preflight as _preflight
    return _preflight.probe_on_pty(argv, {**env, "OS37_STUB_MODE": "version-ok"},
                                    timeout_ms=timeout_ms, cwd=cwd)


def _reap(session) -> None:
    if getattr(session, "pty", None) is None:
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


if __name__ == "__main__":
    unittest.main()
