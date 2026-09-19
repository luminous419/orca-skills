"""OS-37 BUGFIX (run_49c9811cfb6b): one behaviour-based lock per finding of the
consolidated follow-up review of head `87f6179` (issuecomment-5653281497).

Each test FAILS (or ERRORS on an API the fix introduced) at `87f6179` and passes after the
fix, and each reads DURABLE / OS-LEVEL state -- the journal, the ledger, the capture file
and its integrity meta, the process table, the recorded authority -- rather than a source
string.  The crash-recovery cases (F1, F4) drive a REAL SIGKILLed supervisor over a live
child; the authority cases (F2, F3, F9) use two threads / two worktrees and malformed /
truncated / wrong-run authority files; the capture cases (F6, F7) inject a fault on the
real writer; F8 asserts the fingerprint is invariant under a secret-value change and
variant under a config change; F5 asserts the production prompt renderer is delivered at
the `EXECUTE_INTENT -> StandaloneAdapter.start` boundary (the real-CLI graph loop lives in
the gated `test_os37_r10_graph_prompt_e2e.py`).

  1. the Watchdog classifies a crashed standalone dispatch from FENCED liveness evidence,
     reaches STALLED_RECOVERABLE and recovers it -- an open journal row is not a live worker;
  2. the launch authority binds a content-addressed profile digest, and a relaunch with a
     different profile / ledger / approval authority is a create-once conflict;
  3. an unreadable / wrong-run authority refuses every recovery -- never "absent";
  4. adopting a retained LOST dispatch preserves its lost_reason and settles typed;
  5. the production prompt is rendered at the Graph/adapter boundary, not the canonical intent;
  6. a capture write failure is an irreversible unanswerable state;
  7. an inherited capture integrity failure is never healed at handoff;
  8. the preflight fingerprint hashes config + env NAMES, never secret values;
  9. a recovery --standalone-profile is admitted only as an exact-digest restatement.
  L1. refusal detection excludes the runtime's OWN delivered prompt echo.
  B1. (iteration-8 review) the echo is DERIVED from the transport recorded at the
      delivery -- termios read on the pty master, argv vs pty write, framing -- and
      excised only when proven; otherwise `echo_unproven` is named and blocks S3;
      structured records are consulted before any free-text pattern.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import random
import shutil
import signal
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from scripts.deterministic_workflow import (coordinator_liveness, launcher,  # noqa: E402
                                            standalone_capture as capture_mod,
                                            standalone_journal as journal_mod,
                                            standalone_lifecycle as lifecycle,
                                            standalone_preflight as preflight)
from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore  # noqa: E402
from scripts.deterministic_workflow.standalone_profile import (  # noqa: E402
    CaptureLimits, profile_from_mapping)
from scripts.test_os37_external_review_regressions import WORKER_INTENT_KEYS  # noqa: E402
from scripts.test_os37_followup_review_regressions import (  # noqa: E402
    _Composed, _langgraph_ok, LANGGRAPH_REASON, agent_profile_spec, open_fds, pid_alive,
    stub_profile_spec, zombies_among)
from scripts.test_os37_lifecycle_boundary_regressions import INJECTED_REHEARSALS  # noqa: E402
from scripts.deterministic_workflow import standalone_drivers as drivers_mod  # noqa: E402
from scripts.test_os37_lifecycle import (  # noqa: E402
    LIVE as _LIVE, MINTED as _MINTED, DECLARED as _DECLARED, bound as _bound)
from scripts.test_os37_recovery_boundary_regressions import SUPERVISOR, _CrashRoom  # noqa: E402


# =====================================================================================
# F1 -- a crashed standalone run is classified STALLED_RECOVERABLE by a SWEEP and recovered
# =====================================================================================
@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class F1WatchdogSweepRecoversACrashedRunTests(_CrashRoom):
    """[P1] `_StandaloneOrcaState.orca_state` fed the open journal row into
    `active_dispatches`, so a crashed run stayed `ACTIVE_DISPATCH_WAIT` and the sweep never
    reached the adopt -> collect -> settle path.  Red at `87f6179`: the sweep DECLINES."""

    def _profile(self) -> dict:
        return agent_profile_spec(
            worktree=str(self.base / "worktree"),
            driver_env={"OS37_GA_TURN_DELAY_MS": "4000",
                        "OS37_GA_TURN_DELAY_ROLE": "WORKER"},
            timeouts={"completion_timeout_ms": 30000})

    def _sweep(self, run_id: str) -> dict:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            launcher.run_watchdog_cli(["watchdog", "once", "--run-id", run_id,
                                       "--artifact-base", str(self.base),
                                       "--adapter", "standalone", "--json"])
        return json.loads(out.getvalue().strip().splitlines()[-1])["runs"][0]

    def test_a_sweep_reaches_stalled_recoverable_and_recovers_the_crashed_dispatch(self) -> None:
        run_id = "run_f1sweep"
        ledger_path, _profile = self.launch(run_id, self._profile())
        intent_id, spawned = self.await_delivery(run_id)
        agent_pid = int(spawned["source_vocabulary"]["pid"])
        fence = f"{spawned['session_id']}:{spawned['process_incarnation']}"
        spawns_before = [row for row in self.journal(run_id).rows_for(intent_id)
                         if row["event"] == "spawned"]
        # A Coordinator liveness lease, as an Orca turn boundary publishes -- so the gate's
        # premise (an EXPIRED Coordinator) can hold once the crash kills the beat thread.
        keeper = coordinator_liveness.begin_coordinator_liveness(
            run_id, artifact_base=self.base, lease_seconds=self.LEASE_SECONDS)
        self.kill_supervisor()
        if keeper is not None:
            keeper.stop()
        # Wait for the agent to finish and the exit watcher to land the sentinel.
        deadline = time.time() + 30
        while time.time() < deadline and pid_alive(agent_pid):
            time.sleep(0.1)
        time.sleep(self.LEASE_SECONDS + 0.5)             # every dead-owner lease lapses
        self.assertEqual(coordinator_liveness.liveness_status(run_id, artifact_base=self.base),
                         "EXPIRED")
        # The open row is still there, but the worker has exited -- so the sweep must NOT
        # read it as live.
        self.assertEqual(self.journal(run_id).open_dispatches(), (intent_id,))
        fds_before = open_fds()
        run = self._sweep(run_id)
        self.assertEqual(run["state"], "STALLED_RECOVERABLE",
                         f"the crashed run was classified {run['state']}, not recoverable")
        self.assertEqual(run["gate_action"], "ACT", run)
        self.assertTrue(run["acted"], run)
        self.assertEqual(run["outcome_status"], "RECOVERED", run)
        settled = [row for row in self.journal(run_id).rows_for(intent_id)
                   if row["kind"] == "SETTLEMENT_OBSERVED"]
        self.assertEqual(len(settled), 1, "the dispatch was not settled exactly once")
        self.assertEqual(self.journal(run_id).open_dispatches(), ())
        # ---- B5: no respawn, fence preserved, no zombie, no leaked descriptors --------
        spawns_after = [row for row in self.journal(run_id).rows_for(intent_id)
                        if row["event"] == "spawned"]
        self.assertEqual(len(spawns_after), len(spawns_before), "the recovery RE-SPAWNED")
        self.assertEqual(int(spawns_after[0]["source_vocabulary"]["pid"]), agent_pid,
                         "the spawn record's pid changed -- a new process was created")
        self.assertEqual(
            f"{settled[0]['session_id']}:{settled[0]['process_incarnation']}", fence,
            "the settlement is not fenced to the crashed dispatch's incarnation")
        leader = int((spawned["source_vocabulary"].get("spawn_record") or {}).get("sid") or 0)
        self.assertEqual(zombies_among(agent_pid, leader), [],
                         "the recovery left a zombie")
        self.assertEqual(open_fds() - fds_before, set(), "the recovery leaked descriptors")
        self.assertFalse(pid_alive(agent_pid), "the agent is still alive after settlement")
        with contextlib.suppress(OSError):
            os.kill(agent_pid, signal.SIGKILL)

    def test_a_live_worker_is_still_active_dispatch_wait(self) -> None:
        """The other direction: while the worker is genuinely live and fenced, the sweep
        reports work in flight, not a stall.  This is what keeps the fix from turning every
        in-flight dispatch into a recovery target."""
        run_id = "run_f1live"
        self.launch(run_id, self._profile())
        intent_id, spawned = self.await_delivery(run_id)
        agent_pid = int(spawned["source_vocabulary"]["pid"])
        self.assertTrue(pid_alive(agent_pid))
        run = self._sweep(run_id)                          # supervisor still alive, worker live
        self.assertEqual(run["state"], "ACTIVE_DISPATCH_WAIT", run)
        self.assertFalse(run["acted"], run)


# =====================================================================================
# F5 -- the production prompt is rendered at the Graph/adapter boundary
# =====================================================================================
class _RecordingSession:
    """A session that records the payload `start` composes for it and settles trivially."""

    def __init__(self, intent: dict[str, Any]) -> None:
        self.intent = intent
        self.dispatch_id = "d"
        self.task_id = intent.get("task_id", "t")
        self.session_id = "s"
        self.fence = "s:i"
        self.worktree_path = "/tmp"
        self.delivered_payload: Any = "UNSET"

    def run_dispatch(self, *, lease_token: Any = None, payload: Any = None,
                     **kwargs: Any) -> dict[str, Any]:
        self.delivered_payload = payload
        return {"intent_id": self.intent["intent_id"], "settled": True,
                "event_id": "e", "outcome": "succeeded"}


class _RecordingRuntime:
    def __init__(self) -> None:
        self.run_id = "run_f5unit"
        self.session: _RecordingSession | None = None

    def session_for(self, intent: dict[str, Any]) -> _RecordingSession:
        self.session = _RecordingSession(intent)
        return self.session


class F5ProductionPromptRenderedAtBoundaryTests(unittest.TestCase):
    """[P1] `StandaloneAdapter.start` handed the runtime the canonical `ActionIntent` JSON.
    Red at `87f6179`: `StandaloneAdapter` has no `prompt_composer` parameter, so composing
    one is a TypeError; and `start` delivers `None`/the canonical payload, never a rendered
    role/phase/task-contract prompt."""

    def _intent(self, role: str = "WORKER", round_kind: str = "PHASE_GATE") -> dict[str, Any]:
        return {"intent_id": "i1", "run_id": "run_f5unit", "task_id": "t",
                "role": role, "phase": "DESIGN", "gate_iteration": 1,
                "round_kind": round_kind, "repair_instruction": None,
                "payload_digest": "d"}

    def _adapter(self, composer: Any):
        from scripts.deterministic_workflow.standalone_adapter import StandaloneAdapter
        runtime = _RecordingRuntime()
        adapter = StandaloneAdapter(runtime, prompt_composer=composer)
        return adapter, runtime

    def test_start_delivers_the_rendered_production_prompt(self) -> None:
        composer = launcher.build_standalone_prompt_composer(
            objective="Write DESIGN.md documenting function add(a,b).",
            requested_phases=("design",), risk="high")
        adapter, runtime = self._adapter(composer)
        adapter.start(self._intent())
        payload = runtime.session.delivered_payload
        self.assertIsInstance(payload, str)
        self.assertIn("You are the WORKER", payload)
        self.assertIn("TASK CONTRACT", payload)
        self.assertIn("add(a,b)", payload)
        self.assertIn("STATUS: COMPLETE", payload)
        self.assertIn("DECISION GATE CONTRACT", payload)
        # It is NOT the canonical intent JSON.
        self.assertNotIn('"payload_digest"', payload)

    def test_a_reviewer_prompt_carries_the_review_output_contract(self) -> None:
        composer = launcher.build_standalone_prompt_composer(
            objective="Review the design.", requested_phases=("design",), risk="high")
        adapter, runtime = self._adapter(composer)
        adapter.start(self._intent(role="PHASE_REVIEWER"))
        payload = runtime.session.delivered_payload
        self.assertIn("RESULT: PASS", payload)
        self.assertIn("REVIEW.md", payload)

    def test_no_renderer_keeps_the_canonical_payload(self) -> None:
        adapter, runtime = self._adapter(None)
        adapter.start(self._intent())
        # With no renderer the adapter delivers None, so run_dispatch composes the
        # canonical intent itself, exactly as before -- the fake/scripted-path behaviour.
        self.assertIsNone(runtime.session.delivered_payload)


# =====================================================================================
# F2 / F3 / F9 -- the immutable, profile-bound launch authority
# =====================================================================================
class F2F3F9LaunchAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-f2f3f9-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        for name in ("a", "b"):
            (self.base / f"wt-{name}").mkdir()
        self.spec_a = stub_profile_spec("alive", worktree=str(self.base / "wt-a"))
        self.spec_b = stub_profile_spec("alive", worktree=str(self.base / "wt-b"))
        self.ledger_a = FileRuntimeStateStore(self.base / "ledger-a.json")
        self.ledger_b = FileRuntimeStateStore(self.base / "ledger-b.json")

    def _pause_journal(self, run_id: str) -> Any:
        return launcher._standalone_pause_row_journal(self.base, run_id)

    def test_each_thread_recovers_its_own_profile_not_anothers(self) -> None:
        run_id = "run_f2"
        launcher.publish_standalone_launch_bindings(
            self.base, run_id, profile_spec=self.spec_a,
            runtime_state_path=self.ledger_a.path, thread_id="t1")
        launcher.publish_standalone_launch_bindings(
            self.base, run_id, profile_spec=self.spec_b,
            runtime_state_path=self.ledger_b.path, thread_id="t2")
        record = launcher.load_standalone_authority(self.base, run_id, "t1")
        self.assertTrue(record.get("profile_digest"), "the authority binds no profile digest")
        adapter, _j, _p = launcher.standalone_recovery_composition(
            self.base, run_id, thread_id="t1", ledger=self.ledger_a,
            pause_row_journal=self._pause_journal(run_id))
        self.assertEqual(adapter.runtime.profile.worktree, str(self.base / "wt-a"),
                         "recovering t1 rebuilt another thread's profile")

    def test_a_relaunch_with_a_different_profile_is_a_conflict(self) -> None:
        run_id = "run_f2conf"
        launcher.publish_standalone_launch_bindings(
            self.base, run_id, profile_spec=self.spec_a,
            runtime_state_path=self.ledger_a.path, thread_id="t")
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.check_standalone_authority(
                self.base, run_id, runtime_state_path=self.ledger_a.path, thread_id="t",
                approval_authority="none", profile_digest=launcher.profile_digest(self.spec_b))
        self.assertIn(launcher.STANDALONE_AUTHORITY_CONFLICT, str(caught.exception))

    def test_an_unreadable_authority_refuses_every_recovery(self) -> None:
        run_id = "run_f3"
        launcher.publish_standalone_launch_bindings(
            self.base, run_id, profile_spec=self.spec_a,
            runtime_state_path=self.ledger_a.path, thread_id="t")
        launcher.standalone_authority_path(self.base, run_id, "t").write_text("{ not json")
        for selected in ("fake", "orca"):
            with self.assertRaises(launcher.LauncherError):
                launcher.refuse_foreign_composition(self.base, run_id, "t", selected=selected)

    def test_a_wrong_run_authority_is_refused_not_loaded(self) -> None:
        run_id = "run_f3wrong"
        target = launcher.standalone_authority_path(self.base, run_id, "t")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(launcher._authority_record(
            "run_other", runtime_state_path=self.ledger_a.path, thread_id="t",
            approval_authority="none", profile_digest=launcher.profile_digest(self.spec_a))))
        with self.assertRaises(launcher.LauncherError):
            launcher.load_standalone_authority(self.base, run_id, "t")

    def test_a_recovery_profile_override_must_match_the_bound_digest(self) -> None:
        run_id = "run_f9"
        launcher.publish_standalone_launch_bindings(
            self.base, run_id, profile_spec=self.spec_a,
            runtime_state_path=self.ledger_a.path, thread_id="t")
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.standalone_recovery_composition(
                self.base, run_id, thread_id="t", ledger=self.ledger_a,
                pause_row_journal=self._pause_journal(run_id), profile_override=self.spec_b)
        self.assertIn(launcher.STANDALONE_PROFILE_DIGEST_MISMATCH, str(caught.exception))
        # An exact restatement is admitted.
        adapter, _j, _p = launcher.standalone_recovery_composition(
            self.base, run_id, thread_id="t", ledger=self.ledger_a,
            pause_row_journal=self._pause_journal(run_id), profile_override=self.spec_a)
        self.assertEqual(adapter.runtime.profile.worktree, str(self.base / "wt-a"))

    def test_the_resume_verb_accepts_standalone_profile(self) -> None:
        """Finding 9: the flag is no longer rejected by argparse -- it reaches the verb
        (where a missing run is a normal PAUSE_RECORD_MISSING, not an unrecognised-arg
        error)."""
        err = io.StringIO()
        code = None
        with contextlib.redirect_stderr(err), contextlib.suppress(SystemExit):
            code = launcher.run_pause_cli(
                ["resume", "--run-id", "run_f9resume", "--artifact-base", str(self.base),
                 "--adapter", "standalone", "--standalone-profile",
                 str(self.base / "nope.json")])
        self.assertNotIn("unrecognized arguments", err.getvalue())
        self.assertNotEqual(code, None)


# =====================================================================================
# F6 / F7 -- the irreversible capture unanswerable states
# =====================================================================================
class F6F7CaptureIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-f6f7-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.limits = CaptureLimits()

    def test_a_write_failure_is_an_irreversible_unanswerable_state(self) -> None:
        path = self.base / "capture.log"
        store = capture_mod.BoundedCapture(path, limits=self.limits)
        store.append(b'{"type":"system"}\n', at="t0")
        appender = capture_mod.RawBoundedAppender(os.fsencode(str(path)), limits=self.limits)
        real_write = os.write

        def failing(fd: int, data: bytes) -> int:
            if fd == appender.fd:
                raise OSError(28, "No space left on device")
            return real_write(fd, data)
        os.write = failing
        try:
            appender.append(b'{"type":"result","is_error":false}\n')
        finally:
            os.write = real_write
        appender.close()
        reader = capture_mod.BoundedCapture(path, limits=self.limits)
        answer = reader.completion_is_answerable()
        self.assertFalse(answer["answerable"], answer)
        self.assertEqual(answer["lost_reason"], capture_mod.CAPTURE_INTEGRITY_LOST_REASON)
        self.assertGreater(reader.dropped_bytes, 0, "dropped bytes were not counted")
        # Irreversible: even a fresh reader over the same file refuses it.
        self.assertFalse(capture_mod.BoundedCapture(
            path, limits=self.limits).integrity()["consistent"])

    def test_an_inherited_integrity_failure_is_not_healed_at_handoff(self) -> None:
        path = self.base / "capture7.log"
        store = capture_mod.BoundedCapture(path, limits=self.limits)
        store.append(b'{"type":"result","is_error":false}\n', at="t0")
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b"false", b"true "))       # same length, other bytes
        before = capture_mod.BoundedCapture(path, limits=self.limits).completion_is_answerable()
        self.assertFalse(before["answerable"], "the mutation should already be a mismatch")
        appender = capture_mod.RawBoundedAppender(os.fsencode(str(path)), limits=self.limits)
        appender.append(b"tail\n")
        appender.close()
        after = capture_mod.BoundedCapture(path, limits=self.limits).completion_is_answerable()
        self.assertFalse(after["answerable"],
                         "the handoff HEALED a pre-existing integrity failure")
        self.assertTrue(after["integrity"].startswith(capture_mod.UNANSWERABLE_INHERITED_PREFIX),
                        after)


# =====================================================================================
# F8 -- the preflight fingerprint is secret-safe
# =====================================================================================
class F8FingerprintIsSecretSafeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-f8-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.profile = profile_from_mapping({
            **stub_profile_spec("alive", worktree=str(self.base)),
            "auth_secret_ref": {"ANTHROPIC_API_KEY": "SRC_KEY"}})

    def test_the_fingerprint_is_invariant_under_a_secret_value_change(self) -> None:
        env_one = {"PATH": "/usr/bin", "HOME": str(self.base), "ANTHROPIC_API_KEY": "sk-one"}
        env_two = {**env_one, "ANTHROPIC_API_KEY": "sk-two"}
        self.assertEqual(preflight.preflight_fingerprint(self.profile, env_one),
                         preflight.preflight_fingerprint(self.profile, env_two),
                         "the fingerprint changed with only a SECRET VALUE -- a credential "
                         "oracle in the journal")

    def test_the_fingerprint_is_variant_under_a_config_change(self) -> None:
        env = {"PATH": "/usr/bin", "HOME": str(self.base), "ANTHROPIC_API_KEY": "sk-one"}
        env_extra_name = {**env, "A_NEW_NAME": "value"}
        self.assertNotEqual(preflight.preflight_fingerprint(self.profile, env),
                            preflight.preflight_fingerprint(self.profile, env_extra_name),
                            "the fingerprint ignored a new environment NAME")


# =====================================================================================
# F-002 (supersedes L1/B3) -- refusal exclusion is bound to STRUCTURED delivery provenance
# =====================================================================================
# ---- F-002 / B1 transport helpers: the EchoTransport records the runtime writes at delivery
def _tio(**over: Any) -> dict:
    """A termios evidence block as `standalone_pty.termios_evidence` returns it: every flag
    named, so `expected_echo_forms` derives the echo from EVIDENCE and never from a
    default.  The base is an echoing pty in non-canonical mode with ONLCR (what a spawned
    slave looks like once an agent turns ECHO on)."""
    base = {"echo": True, "echoctl": True, "echonl": False, "icanon": False, "isig": False,
            "iexten": False, "ixon": False, "opost": True, "onlcr": True, "ocrnl": False,
            "onocr": False, "onlret": False, "tab_expand": False, "icrnl": True,
            "inlcr": False, "igncr": False, "special_bytes": [],
            # The LINE DISCIPLINE the flags were read from (iteration-3 CI correction): the
            # BSD `ttydisc` (macOS) by default; `_linux()` builds the Linux `n_tty` variant.
            "platform": "Darwin", "discipline": "bsd_ttydisc"}
    base.update(over)
    return base


def _pty(framed: bool = False, **tio: Any) -> dict:
    return {"kind": "pty_write", "framed": framed, "termios": _tio(**tio), "cols": 120}


def _linux(framed: bool = False, **tio: Any) -> dict:
    """The same evidence block as read on a Linux pty (`n_tty`)."""
    return _pty(framed=framed, platform="Linux", discipline="linux_n_tty", **tio)


ARGV_TRANSPORT = {"kind": "argv", "framed": False, "termios": None, "cols": 120}
RAW_PTY = _pty(echo=False)                    # what `_set_raw` leaves: ECHO clear


class F002RefusalExclusionIsEventBoundTests(unittest.TestCase):
    """Final Adversarial Review F-002, hardened through the iteration-8 review (B1).

    Iteration 5 bound exclusion to a bracketed-paste FRAME (not provenance -- an agent can
    print those bytes).  Iterations 6-7 bound it to a delivery EVENT but mixed a raw-byte
    delivery offset with indices into decoded/sanitised TEXT.  Iteration 8 matched in ONE
    coordinate space (raw bytes) but byte-for-byte, assuming the pty echoes a write verbatim.

    The contract is now: a span is excised ONLY when it (a) begins at/after the recorded
    RAW-BYTE delivery offset, (b) the event carries the TRANSPORT recorded at the write and
    (c) the span equals a form DERIVED from the delivered bytes under that transport, and
    (d) it is the only such span in the delivery window.  Without a transport record the
    echo is `echo_unproven:transport_unrecorded` -- named, nothing excised, fail closed.
    Exclusion happens BEFORE decode; the removed content API stays gone.

    **Superseded, strictly stronger:** every exclusion-expecting case below now carries the
    transport that proves the echo; the iteration-8 form (event = offset + payload, no
    transport) is re-asserted as `echo_unproven` -- a bare offset is no longer sufficient
    provenance, because the same bytes at the same offset are also what a forged echo looks
    like when the pty could not have echoed at all."""

    @staticmethod
    def _ev(payload: str, offset: int = 0, transport: dict | None = None) -> tuple[dict, ...]:
        event = {"offset": offset, "payload": payload}
        if transport is not None:
            event["transport"] = transport
        return (event,)

    @staticmethod
    def _cic(raw: bytes, events: tuple = ()) -> tuple:
        return lifecycle.classify_refusals_in_capture(raw, events)

    def _echo_state(self, raw: bytes, events: tuple) -> str:
        return lifecycle.resolve_delivery_echo(raw, events)["state"]

    def test_the_content_exclusion_api_is_gone(self) -> None:
        self.assertFalse(hasattr(lifecycle, "strip_echoed"),
                         "the content-based strip_echoed must be replaced")
        self.assertNotIn("exclude_text", lifecycle.classify_refusals.__code__.co_varnames,
                         "classify_refusals must not carry the content-exclusion parameter")

    def test_strip_delivery_echo_is_byte_addressable(self) -> None:
        # The primitive operates on RAW BYTES in one coordinate space, returning bytes --
        # and excises only under a transport that proves the echo.
        self.assertEqual(lifecycle.strip_delivery_echo(
            b"login required", self._ev("login required", 0, _pty())), b"")
        self.assertEqual(lifecycle.strip_delivery_echo(
            b"login required", self._ev("login required")), b"login required",
            "an event with no transport record proves nothing and excises nothing")
        self.assertIsInstance(lifecycle.strip_delivery_echo(b"x", ()), bytes)

    def test_the_review_probe_a_bare_refusal_string_still_fires(self) -> None:
        self.assertTrue(lifecycle.classify_refusals("login required"),
                        "a bare refusal string must fire")
        self.assertTrue(self._cic(b"login required", ()),
                        "with NO delivery event nothing is subtracted (fail closed)")

    def test_a_proven_echo_at_the_recorded_byte_offset_is_excluded(self) -> None:
        raw = b"login required\r\nok\r\n"
        self.assertEqual(self._cic(raw, self._ev("login required", 0, _pty())), (),
                         "the echo of the delivered payload at its byte offset must not fire")
        # Iteration-8 form (offset only): unproven BY NAME, nothing excised, the scan fires.
        bare = self._ev("login required")
        self.assertTrue(self._cic(raw, bare))
        res = lifecycle.resolve_delivery_echo(raw, bare)
        self.assertEqual((res["state"], res["reason"]),
                         ("echo_unproven", "event[0]:transport_unrecorded"))

    def test_a_marker_shaped_span_without_an_event_is_not_excluded(self) -> None:
        forged = b"\x1b[200~login required\x1b[201~\r\nok\r\n"
        self.assertTrue(self._cic(forged, ()),
                        "a marker-shaped span an agent emitted must not be excluded")
        self.assertTrue(self._cic(forged, self._ev("a totally different prompt", 0, _pty())),
                        "an event for a different payload must not exclude the forged frame")

    def test_the_iteration7_bypass_esc_before_a_pre_delivery_refusal_still_fires(self) -> None:
        # The iteration-7 reviewer's EXACT mutation, in raw-byte space: a raw ESC byte then a
        # refusal at byte 1, delivery baseline at byte 4.  The refusal is PRE-delivery, so it
        # must fire.  A raw-byte offset must never be compared with a sanitized-char index.
        raw = b"\x1blogin required"                        # ESC(1) + "login required"@byte 1
        self.assertTrue(self._cic(raw, self._ev("login required", 4, _pty())),
                        "an ESC before a pre-delivery refusal must not shift it past the "
                        "byte offset and get it excluded")

    def test_multi_byte_utf8_before_a_pre_delivery_refusal_still_fires(self) -> None:
        raw = "café ☕ ".encode("utf-8") + b"login required"
        # baseline well past the refusal's byte start, but still pre-delivery for it.
        self.assertTrue(self._cic(raw, self._ev("login required", len(raw) + 100, _pty())),
                        "multi-byte UTF-8 before a pre-delivery refusal must not hide it")

    def test_a_sanitised_esc_token_inside_the_delivered_payload_is_matched(self) -> None:
        # A payload that carried a raw ESC is written as `<ESC>` (5 bytes) by sanitize_payload;
        # the echo in the capture is those bytes.  The event carries the RAW payload; the
        # matcher forms the delivered bytes itself and excises the echo.
        payload = "\x1blogin required"
        echoed = "<ESC>login required".encode("utf-8") + b"\r\nok\r\n"
        self.assertEqual(self._cic(echoed, self._ev(payload, 0, _pty())), (),
                         "the <ESC>-substituted echo of the delivered payload must be excluded")

    def test_the_echo_straddling_a_chunk_boundary_is_still_excluded(self) -> None:
        # Two capture chunks whose concatenation contains the echo at the recorded offset:
        # exclusion is on the concatenated raw bytes, so a chunk split inside the echo does
        # not defeat it.
        chunk1 = b"prelude\r\nlogin req"
        chunk2 = b"uired\r\ntrailer"
        raw = chunk1 + chunk2
        offset = raw.index(b"login required")
        self.assertEqual(self._cic(raw, self._ev("login required", offset, _pty())), (),
                         "an echo split across a chunk boundary must still be excluded")

    def test_a_refusal_before_the_byte_offset_survives_the_echo_after_it(self) -> None:
        raw = b"login required\r\nXXX\r\nlogin required"
        offset = raw.rindex(b"login required")             # the echo is the LAST occurrence
        events = self._ev("login required", offset, _pty())
        self.assertEqual(self._echo_state(raw, events), "echo_proven")
        self.assertTrue(self._cic(raw, events),
                        "the pre-offset refusal must survive while the echo at offset is excised")

    def test_only_the_payload_bytes_are_removed_not_an_adjacent_refusal(self) -> None:
        payload = "here is your task, do it well"
        raw = payload.encode("utf-8") + b"login required"
        self.assertTrue(self._cic(raw, self._ev(payload, 0, _pty())),
                        "a refusal glued after the echo survives; only the payload span goes")

    def test_an_unmatched_event_payload_fails_closed(self) -> None:
        events = self._ev("THE DELIVERED PROMPT WAS NEVER ECHOED", 0, _pty())
        self.assertTrue(self._cic(b"login required\r\n", events),
                        "with no proven echo the scan subtracts nothing (fail closed)")
        res = lifecycle.resolve_delivery_echo(b"login required\r\n", events)
        self.assertEqual((res["state"], res["reason"]),
                         ("echo_unproven", "event[0]:echo_not_found"))

    def test_a_real_claude_shaped_no_echo_capture_is_read_correctly(self) -> None:
        delivered = "Role: worker. Objective: add two numbers. Return JSON."
        benign = (b'{"type":"system","session_id":"s1"}\r\n'
                  b'{"type":"assistant","text":"working"}\r\n'
                  b'{"type":"result","is_error":false}\r\n')
        self.assertNotIn(b"\x1b[200~", benign)              # like the real captures: no markers
        events = self._ev(delivered, 0, ARGV_TRANSPORT)      # the prompt left with the execve
        self.assertEqual(self._cic(benign, events), ())
        self.assertEqual(self._echo_state(benign, events), "echo_absent",
                         "an argv delivery PROVES no line-discipline echo: nothing unproven")
        self.assertTrue(self._cic(benign + b"login required\r\n", events),
                        "a genuine refusal in a no-echo real-shaped capture must fire")

    def test_property_random_prefixes_never_hide_a_pre_baseline_refusal(self) -> None:
        # For random byte prefixes containing ESC / multi-byte / control bytes: a refusal
        # placed BEFORE the delivery baseline is NEVER excluded, and a genuine echo AT the
        # baseline is ALWAYS excluded.  One coordinate space (raw bytes) makes both hold.
        rng = random.Random(20260914)
        pool = [b"\x1b", b"\x07", b"\x00", b"\x1b[0m", b"\xc3\xa9", b"\xe2\x9c\x93",
                b"x", b" ", b"\r\n", b"\t", b".", b"#"]
        refusal = b"login required"
        for _ in range(500):
            prefix = b"".join(rng.choice(pool) for _ in range(rng.randint(0, 12)))
            # (A) echo AT the baseline, nothing before it -> excluded -> no fire.  The needle
            # begins exactly at the baseline regardless of what bytes precede it.
            raw_a = prefix + refusal
            self.assertEqual(self._cic(raw_a, self._ev("login required", len(prefix), _pty())),
                             (), f"echo at baseline not excluded; prefix={prefix!r}")
            # (B) a refusal BEFORE the baseline (on its own line, so it is line-anchored) plus
            # the echo at the baseline -> the echo is excised but the pre-baseline refusal
            # fires.  This is the security property: a pre-delivery refusal is never hidden by
            # a byte-vs-char coordinate shift, whatever ESC/multi-byte/control bytes precede it.
            head = prefix + b"\r\n" + refusal + b"\r\n"
            raw_b = head + refusal
            self.assertTrue(self._cic(raw_b, self._ev("login required", len(head), _pty())),
                            f"pre-baseline refusal wrongly hidden; prefix={prefix!r}")

    def _s3(self, ev: dict) -> dict:
        return lifecycle.may_send_prompt(ev, minted_session_id=_MINTED,
                                         declared_record_types=_DECLARED)

    def test_forged_exact_payload_frame_outside_the_span_denies_ready_via_may_send_prompt(self) -> None:
        # Iteration-6/7 bypass through may_send_prompt, now in byte space: a valid R-A/R-B
        # quorum, a delivery event at a DISTANT raw-byte offset, and an agent-forged frame in
        # the supplementary preview carrying the EXACT payload -- plus an ESC before it, the
        # iteration-7 twist.  The refusal must still fire; `ready` must be denied.
        forged_raw = b"\x1blogin required\r\nok"
        attack = {"liveness": _LIVE, "bound_signal": _bound(), "refusals": (),
                  "delivery_events": ({"offset": 9999, "payload": "login required",
                                       "transport": _pty()},),
                  "supplementary": ({"tier": "screen_preview", "text": "<forged>",
                                     "raw": forged_raw, "start_offset": 0,
                                     "live_observed": True, "at": ""},)}
        self.assertNotEqual(self._s3(attack)["verdict"], "ready",
                            "a forged exact-payload frame outside the delivery span must not "
                            "erase the refusal through may_send_prompt")

    def test_a_legit_echo_at_the_recorded_offset_is_excluded_via_may_send_prompt(self) -> None:
        # Control: the echo at the recorded byte offset (translated into the preview's byte
        # coordinate) is excluded, so a quorum with only the echo present reaches ready.
        prefix = b"\x1bpreamble\r\n"                        # includes an ESC before the echo
        preview_raw = prefix + b"login required"
        start_offset = 40
        ok = {"liveness": _LIVE, "bound_signal": _bound(), "refusals": (),
              "delivery_events": ({"offset": start_offset + len(prefix),
                                   "payload": "login required", "transport": _pty()},),
              "supplementary": ({"tier": "screen_preview", "text": "preview",
                                 "raw": preview_raw, "start_offset": start_offset,
                                 "live_observed": True, "at": ""},)}
        self.assertEqual(self._s3(ok)["verdict"], "ready",
                         "the echo at the recorded (translated) byte offset must be excluded")

    def test_a_supplementary_without_raw_bytes_adds_no_refusal(self) -> None:
        # Provenance requires raw bytes; a text-only observation cannot establish it and must
        # add nothing beyond the authoritative refusals (never clamp / char-length arithmetic).
        ev = {"liveness": _LIVE, "bound_signal": _bound(), "refusals": (),
              "delivery_events": ({"offset": 0, "payload": "login required",
                                   "transport": _pty()},),
              "supplementary": ({"tier": "screen_preview", "text": "login required",
                                 "start_offset": 0, "live_observed": True, "at": ""},)}
        self.assertEqual(self._s3(ev)["verdict"], "ready",
                         "a text-only observation must not re-derive a refusal by content")

    def test_the_driver_readiness_scan_binds_to_raw_bytes_end_to_end(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="os37-f2drv-"))
        self.addCleanup(shutil.rmtree, base, True)
        (base / "wt").mkdir()
        profile = profile_from_mapping(stub_profile_spec("alive", worktree=str(base / "wt")))
        driver = drivers_mod.driver_for(profile)
        payload = "please run the task, login required to continue"
        ev = ({"offset": 0, "payload": payload, "transport": _pty()},)
        # Echo present at byte 0 -> excluded (raw supplied) -> no refusal.
        evidence = driver.readiness_evidence(
            payload + "\r\n", minted_session_id="s1", liveness=None,
            raw=(payload + "\r\n").encode("utf-8"), delivery_events=ev)
        self.assertEqual(tuple(evidence["refusals"]), (),
                         "the delivered-payload echo must be excluded on raw bytes")
        self.assertEqual(evidence["echo"]["state"], "echo_proven")
        # A pre-delivery refusal preceded by an ESC byte, delivery baseline past it -> fires.
        raw = b"\x1blogin required\r\n"
        evidence2 = driver.readiness_evidence(
            raw.decode("utf-8", "replace"), minted_session_id="s1", liveness=None,
            raw=raw, delivery_events=({"offset": 99, "payload": "login required",
                                       "transport": _pty()},))
        self.assertTrue(tuple(evidence2["refusals"]),
                        "an ESC-preceded pre-delivery refusal must fire through the driver")
        self.assertEqual(evidence2["refusal_source"], "free_text")


# =====================================================================================
# B1 -- delivery-echo provenance is DERIVED from the recorded transport, fail-closed BY NAME
# =====================================================================================
class B1EchoProvenanceIsTransportDerivedTests(unittest.TestCase):
    """Iteration-8 review B1 (`REVIEW_BUGFIX_iteration8.md`): `_delivered_echo_bytes` /
    `strip_delivery_echo` assumed the pty echoes the delivered payload byte-for-byte.  A
    multiline prompt is written with LF and echoed with CRLF (`ONLCR`), so the echo was not
    matched and runtime-authored task text (`login required`) fired as a runtime refusal.

    The model now: the driver records an `EchoTransport` AT the delivery (`argv` /
    `pty_write`, framing, and the pty's live termios read with `tcgetattr` on the master);
    `lifecycle.expected_echo_forms` DERIVES the echo forms from that evidence (`ONLCR`,
    `OCRNL`, `ONOCR`, `ECHOCTL`, tab expansion over every unobservable start column, the
    two kernels' newline/column rule, bracketed-paste framing, line-discipline special
    bytes); `resolve_delivery_echo` excises a span ONLY when exactly one span at/after the
    recorded raw-byte offset equals a derived form.  Anything else is `echo_unproven` with
    a NAMED reason (`ECHO_UNPROVEN_REASONS`), excludes nothing, and makes S3 `unprovable`.
    Structured records are consulted before any free-text pattern (`refusal_evidence`).

    Red on the iteration-8 staged tree (`evidence/B1_RED_at_staged_tree.txt`): the
    reviewer's exact CRLF reproduction returns `('blocked_prompt_beats_idle',)`, the named
    APIs (`resolve_delivery_echo`, `expected_echo_forms`, `refusal_evidence`,
    `standalone_pty.echo_transport`) do not exist, and a tool-result quoting the task text
    fires as a refusal."""

    PAYLOAD = "Task contract:\nlogin required\nContinue"

    @staticmethod
    def _ev(payload: str, offset: int = 0, transport: dict | None = None,
            **extra: Any) -> dict:
        event: dict = {"offset": offset, "payload": payload, **extra}
        if transport is not None:
            event["transport"] = transport
        return event

    def _resolve(self, raw: bytes, *events: dict) -> dict:
        return lifecycle.resolve_delivery_echo(raw, events)

    def _refusals(self, raw: bytes, *events: dict) -> tuple:
        return lifecycle.classify_refusals_in_capture(raw, events)

    def _forms(self, payload: str, transport: dict) -> tuple:
        derived = lifecycle.expected_echo_forms(payload, transport)
        self.assertEqual(derived["state"], "echo_expected", derived)
        return derived["forms"]

    # -- the vocabulary ------------------------------------------------------------------
    def test_the_echo_states_and_unproven_reasons_are_closed_and_named(self) -> None:
        self.assertEqual(lifecycle.ECHO_STATES,
                         ("no_delivery", "echo_absent", "echo_proven", "echo_unproven"))
        self.assertIn("echo_unproven", lifecycle.ECHO_STATES)
        for reason in ("transport_unrecorded", "termios_unreadable", "echo_not_found",
                       "ambiguous_multiple_matches", "delivery_before_window",
                       "control_byte_consumed_by_line_discipline"):
            self.assertIn(reason, lifecycle.ECHO_UNPROVEN_REASONS)
        self.assertEqual(lifecycle.ECHO_TRANSPORT_KINDS, ("argv", "pty_write"))
        # The bracket bytes have ONE definition, shared by the framing function.
        self.assertEqual(drivers_mod.BRACKET_START, lifecycle.BRACKETED_PASTE_START)
        self.assertEqual(drivers_mod.BRACKET_END, lifecycle.BRACKETED_PASTE_END)

    # -- 1. LF and CRLF multiline echo ---------------------------------------------------
    def test_the_reviewers_crlf_reproduction_is_a_proven_echo_not_a_refusal(self) -> None:
        raw = self.PAYLOAD.replace("\n", "\r\n").encode()
        events = (self._ev(self.PAYLOAD, 0, _pty(onlcr=True)),)
        self.assertEqual(lifecycle.strip_delivery_echo(raw, events), b"")
        self.assertEqual(lifecycle.classify_refusals_in_capture(raw, events), (),
                         "runtime-authored task text echoed with CRLF is NOT a refusal")
        res = self._resolve(raw, *events)
        self.assertEqual(res["state"], "echo_proven")
        self.assertEqual(res["spans"], ((0, len(raw)),))

    def test_lf_echo_under_onlcr_clear_is_proven_and_crlf_under_onlcr_set_is_the_only_form(self) -> None:
        lf = self.PAYLOAD.encode()
        crlf = self.PAYLOAD.replace("\n", "\r\n").encode()
        # ONLCR clear: the echo is LF; CRLF is NOT a form (the derivation is from evidence).
        self.assertEqual(self._forms(self.PAYLOAD, _pty(onlcr=False)), (lf,))
        self.assertEqual(self._refusals(lf, self._ev(self.PAYLOAD, 0, _pty(onlcr=False))), ())
        # ONLCR set: the echo is CRLF; an LF span is NOT it -> unproven by name, fires.
        self.assertEqual(self._forms(self.PAYLOAD, _pty(onlcr=True)), (crlf,))
        res = self._resolve(lf, self._ev(self.PAYLOAD, 0, _pty(onlcr=True)))
        self.assertEqual((res["state"], res["reason"]),
                         ("echo_unproven", "event[0]:echo_not_found"))
        self.assertTrue(self._refusals(lf, self._ev(self.PAYLOAD, 0, _pty(onlcr=True))),
                        "an unproven echo excludes nothing; the superset scan fires")
        # OPOST clear disables every output translation: the LF form only.
        self.assertEqual(self._forms(self.PAYLOAD, _pty(opost=False)), (lf,))

    def test_ocrnl_onocr_and_ixon_are_derived_from_the_flags_not_assumed(self) -> None:
        payload = "a\rb"
        # ICRNL clear, OCRNL set: the CR is received as CR and output as LF (measured on a
        # real pty: `a\nb`); under ECHOCTL the received CR is ^-rendered first (`a^Mb`).
        self.assertEqual(self._forms(payload, _pty(icrnl=False, ocrnl=True, echoctl=False)),
                         (b"a\nb",))
        self.assertEqual(self._forms(payload, _pty(icrnl=False, ocrnl=True, echoctl=True)),
                         (b"a^Mb",))
        # ICRNL set (the default): CR becomes NL on input, then ONLCR echoes CRLF.
        self.assertEqual(self._forms(payload, _pty(icrnl=True)), (b"a\r\nb",))
        # IGNCR: the CR is dropped before it can be echoed.
        self.assertEqual(self._forms(payload, _pty(igncr=True)), (b"ab",))
        # ONOCR suppresses a CR at column 0 -- column-tracked, per byte (measured: `x` / `y\rx`).
        self.assertEqual(self._forms("\rx", _pty(icrnl=False, onocr=True, echoctl=False)),
                         (b"x",))
        self.assertEqual(self._forms("y\rx", _pty(icrnl=False, onocr=True, echoctl=False)),
                         (b"y\rx",))
        # IXON arms VSTART/VSTOP as consumed bytes: the transport record names them and a
        # payload carrying one has no derivable echo.
        derived = lifecycle.expected_echo_forms(
            "a\x13b", _pty(ixon=True, special_bytes=[0x11, 0x13]))
        self.assertEqual((derived["state"], derived["reason"]),
                         ("echo_unproven", "control_byte_consumed_by_line_discipline"))

    # -- 2. multibyte UTF-8, incl. split across capture chunks ---------------------------
    def test_multibyte_utf8_echo_split_across_chunks_is_proven_and_excised_whole(self) -> None:
        payload = "café ☕ 한글\nlogin required\n終わり"
        echo = payload.replace("\n", "\r\n").encode("utf-8")
        cut = echo.index("☕".encode("utf-8")) + 1          # split INSIDE the 3-byte char
        chunk1, chunk2 = b"pre\r\n" + echo[:cut], echo[cut:] + b"\r\npost"
        raw = chunk1 + chunk2
        events = (self._ev(payload, 5, _pty()),)
        res = self._resolve(raw, *events)
        self.assertEqual(res["state"], "echo_proven")
        self.assertEqual(raw[res["spans"][0][0]:res["spans"][0][1]], echo)
        self.assertEqual(lifecycle.strip_delivery_echo(raw, events), b"pre\r\n\r\npost")
        self.assertEqual(self._refusals(raw, *events), ())
        # A genuine refusal after the multibyte echo still fires; the pre-offset one too.
        self.assertTrue(self._refusals(raw + b"\r\nlogin required\r\n", *events))
        self.assertTrue(self._refusals(b"login required\r\n" + raw,
                                       self._ev(payload, 21, _pty())))

    # -- 3. ESC / control-sequence expansion in and around the payload -------------------
    def test_control_bytes_are_rendered_as_the_line_discipline_renders_them(self) -> None:
        # A raw ESC in the payload is DELIVERED as `<ESC>` (sanitize_payload) -- no control
        # byte reaches the pty from the payload itself.  Other control bytes are echoed as
        # ^X under ECHOCTL and verbatim without it; TAB and NL are never ^-rendered.
        payload = "\x1b[31mlogin required\x01\t\n"
        self.assertEqual(self._forms(payload, _pty(echoctl=True)),
                         (b"<ESC>[31mlogin required^A\t\r\n",))
        self.assertEqual(self._forms(payload, _pty(echoctl=False)),
                         (b"<ESC>[31mlogin required\x01\t\r\n",))
        # The bracketed-paste FRAME the runtime writes is echoed too, ESC as ^[ under ECHOCTL
        # -- the frame is part of the proven span, so a fake frame elsewhere is not.
        framed = self._forms("login required", _pty(framed=True))
        self.assertEqual(framed, (b"^[[200~login required^[[201~",))
        self.assertEqual(self._forms("login required", _pty(framed=True, echoctl=False)),
                         (b"\x1b[200~login required\x1b[201~",))
        # Agent-authored escape sequences AROUND the proven echo do not disturb it, and an
        # ESC-prefixed genuine refusal after it still fires.
        raw = b"\x1b[2J\x1b[H" + framed[0] + b"\r\n\x1b[1mlogin required\x1b[0m\r\n"
        ev = self._ev("login required", 0, _pty(framed=True))
        self.assertEqual(self._resolve(raw, ev)["state"], "echo_proven")
        self.assertTrue(self._refusals(raw, ev))
        self.assertEqual(self._refusals(b"\x1b[2J\x1b[H" + framed[0] + b"\r\nok\r\n", ev), ())
        # A line-discipline SPECIAL byte in the payload (^C under ISIG) is CONSUMED, not
        # echoed -- it flushes the queue -- so the echo is unproven by name.
        derived = lifecycle.expected_echo_forms(
            "a\x03b", _pty(isig=True, special_bytes=[0x03, 0x1c, 0x1a]))
        self.assertEqual((derived["state"], derived["reason"]),
                         ("echo_unproven", "control_byte_consumed_by_line_discipline"))

    def test_tab_expansion_enumerates_every_unobservable_start_column(self) -> None:
        payload = "ab\tc"
        forms = self._forms(payload, _pty(tab_expand=True))
        self.assertEqual(len(forms), 8, "one form per start column modulo the tab stop")
        self.assertIn(b"ab      c", forms)          # column 0: 2 + 6 spaces
        self.assertIn(b"ab   c", forms)             # column 5 (measured after 5 cols of output)
        self.assertNotIn(b"ab\tc", forms)
        self.assertEqual(self._forms(payload, _pty(tab_expand=False)), (b"ab\tc",))
        # A multi-byte char and a ^X each advance the column by their BYTE count (2).
        self.assertIn("é".encode() + b"      z", self._forms("é\tz", _pty(tab_expand=True)))
        self.assertIn(b"^A      z", self._forms("\x01\tz", _pty(tab_expand=True)))
        # The newline/column rule is the DISCIPLINE's (iteration 3), read from the evidence,
        # never enumerated: BSD resets the column on NL whether or not ONLCR is set; Linux
        # keeps it (and in raw mode under ECHOCTL renders the NL as ^J, two columns).
        self.assertEqual(self._forms("a\n\tb", _pty(tab_expand=True, onlcr=False)),
                         (b"a\n        b",))                     # BSD: NL resets the column
        self.assertEqual(self._forms("a\n\tb", _pty(tab_expand=True, onlcr=True)),
                         (b"a\r\n        b",))
        self.assertIn(b"a\n       b",                             # Linux, ECHOCTL clear: col 1
                      self._forms("a\n\tb", _linux(tab_expand=True, onlcr=False,
                                                    echoctl=False)))
        self.assertIn(b"a^J     b",                                # Linux, ECHOCTL: ^J -> col 3
                      self._forms("a\n\tb", _linux(tab_expand=True, onlcr=False)))
        # A proven tab-expanded echo is excised; the refusal after it fires.
        raw = b"ab   c\r\nlogin required\r\n"
        ev = self._ev(payload, 0, _pty(tab_expand=True))
        self.assertEqual(self._resolve(raw, ev)["state"], "echo_proven")
        self.assertTrue(self._refusals(raw, ev))
        self.assertEqual(self._refusals(b"ab   c\r\nok\r\n", ev), ())

    # -- 4. partial, repeated, delayed echo ----------------------------------------------
    def test_a_partial_echo_is_unproven_by_name_and_excludes_nothing(self) -> None:
        raw = b"Task contract:\r\nlogin req"                # the echo was cut short
        ev = self._ev(self.PAYLOAD, 0, _pty())
        res = self._resolve(raw, ev)
        self.assertEqual((res["state"], res["reason"]),
                         ("echo_unproven", "event[0]:echo_not_found"))
        self.assertEqual(lifecycle.strip_delivery_echo(raw, (ev,)), raw)
        self.assertEqual(self._refusals(raw, ev), (),
                         "the cut-short bytes carry no refusal pattern")
        self.assertTrue(self._refusals(b"Task contract:\r\nlogin required", ev),
                        "a partial echo whose visible bytes match a pattern FIRES")

    def test_a_repeated_echo_in_one_window_is_ambiguous_by_name(self) -> None:
        echo = b"login required\r\n"
        raw = echo + b"ok\r\n" + echo
        ev = self._ev("login required\n", 0, _pty())
        res = self._resolve(raw, ev)
        self.assertEqual((res["state"], res["reason"]),
                         ("echo_unproven", "event[0]:ambiguous_multiple_matches"))
        self.assertEqual(lifecycle.strip_delivery_echo(raw, (ev,)), raw)
        self.assertTrue(self._refusals(raw, ev), "nothing is guessed; the superset fires")

    def test_a_delayed_echo_past_the_next_delivery_window_is_unproven_for_its_event(self) -> None:
        first, second = "first prompt\n", "second prompt\n"
        # The first echo lands AFTER the second event's offset -- outside its window.
        raw = b"agent output\r\n" + b"second prompt\r\n" + b"first prompt\r\n"
        ev1 = self._ev(first, 0, _pty())
        ev2 = self._ev(second, len(b"agent output\r\n"), _pty())
        res = self._resolve(raw, ev1, ev2)
        self.assertEqual(res["state"], "echo_unproven")
        self.assertEqual(res["reason"], "event[0]:echo_not_found")
        per = {e["index"]: e for e in res["events"]}
        self.assertEqual(per[0]["state"], "echo_unproven")
        self.assertEqual(per[1]["state"], "echo_proven", "the second event's echo IS in its window")
        self.assertEqual(lifecycle.strip_delivery_echo(raw, (ev1, ev2)), raw,
                         "one unproven event fails the whole partition closed")
        # In order, both are proven and both excised.
        ordered = b"first prompt\r\n" + b"agent output\r\n" + b"second prompt\r\n"
        ev2b = self._ev(second, len(b"first prompt\r\nagent output\r\n"), _pty())
        self.assertEqual(self._resolve(ordered, ev1, ev2b)["state"], "echo_proven")
        self.assertEqual(lifecycle.strip_delivery_echo(ordered, (ev1, ev2b)),
                         b"agent output\r\n")

    # -- 5. forged output carrying the same payload --------------------------------------
    def test_a_forged_payload_outside_the_delivery_span_is_never_excluded(self) -> None:
        echo = b"login required\r\n"
        # Before the offset: the agent printed the exact payload before the write.
        raw = b"forged: " + echo + b"real: " + echo
        ev = self._ev("login required\n", len(b"forged: " + echo), _pty())
        res = self._resolve(raw, ev)
        self.assertEqual(res["state"], "echo_proven")
        self.assertEqual(res["spans"], ((raw.rindex(echo), len(raw)),))
        self.assertTrue(self._refusals(raw, ev), "the forged copy before the offset fires")

    def test_a_forged_frame_when_the_pty_could_not_echo_is_never_excluded(self) -> None:
        fake = b"\x1b[200~login required\x1b[201~\r\n"
        # The runtime's own spawn leaves ECHO clear (`_set_raw`): the transport PROVES no
        # echo, so the frame-shaped bytes are the agent's and fire.
        ev = self._ev("login required", 0, RAW_PTY)
        res = self._resolve(fake, ev)
        self.assertEqual((res["state"], res["events"][0]["reason"]),
                         ("echo_absent", "echo_flag_clear"))
        self.assertTrue(self._refusals(fake, ev))
        # argv delivery: the line discipline never saw the payload.
        res = self._resolve(fake, self._ev("login required", 0, ARGV_TRANSPORT))
        self.assertEqual((res["state"], res["events"][0]["reason"]),
                         ("echo_absent", "argv_transport_cannot_echo"))
        self.assertTrue(self._refusals(fake, self._ev("login required", 0, ARGV_TRANSPORT)))
        # ECHO set with ECHOCTL: the real echo would render ESC as ^[; a RAW-ESC frame is not
        # the derived form -> unproven by name, and it fires.
        ev = self._ev("login required", 0, _pty(framed=True))
        res = self._resolve(fake, ev)
        self.assertEqual((res["state"], res["reason"]),
                         ("echo_unproven", "event[0]:echo_not_found"))
        self.assertTrue(self._refusals(fake, ev))

    # -- 6. prompt and a genuine refusal containing the same string, both orders ---------
    def test_the_same_string_as_prompt_and_as_genuine_refusal_fires_in_both_orders(self) -> None:
        line = b"login required\r\n"
        ev_at = lambda off: self._ev("login required\n", off, _pty())   # noqa: E731
        # echo THEN refusal (both after the offset): two identical spans -> ambiguous, fires.
        raw = line + line
        res = self._resolve(raw, ev_at(0))
        self.assertEqual(res["reason"], "event[0]:ambiguous_multiple_matches")
        self.assertTrue(self._refusals(raw, ev_at(0)))
        # refusal THEN echo, refusal before the offset: proven echo, pre-offset refusal fires.
        res = self._resolve(raw, ev_at(len(line)))
        self.assertEqual(res["state"], "echo_proven")
        self.assertTrue(self._refusals(raw, ev_at(len(line))))
        # echo THEN a DIFFERENT genuine refusal: proven, excised, the refusal fires.
        raw2 = line + b"You are not logged in.\r\n"
        self.assertEqual(self._resolve(raw2, ev_at(0))["state"], "echo_proven")
        self.assertTrue(self._refusals(raw2, ev_at(0)))
        # the control: echo alone is nothing.
        self.assertEqual(self._refusals(line, ev_at(0)), ())

    # -- 7. raw-byte offset vs sanitized-character offset disagreement -------------------
    def test_a_raw_byte_offset_is_never_reconciled_with_a_sanitized_character_index(self) -> None:
        # ESC (1 byte, rendered as 5 chars) and multi-byte chars BEFORE the offset: the
        # character index of the refusal is far past the byte offset; the byte offset wins.
        prefix = b"\x1b\x1b\x1b" + "☕☕".encode() + b"\r\n"     # 3 + 6 + 2 = 11 bytes
        refusal = b"login required\r\n"
        raw = prefix + refusal + refusal
        offset = len(prefix) + len(refusal)                     # the SECOND line is the echo
        ev = self._ev("login required\n", offset, _pty())
        res = self._resolve(raw, ev)
        self.assertEqual(res["spans"], ((offset, len(raw)),))
        self.assertTrue(self._refusals(raw, ev), "the pre-offset refusal fires")
        # The same offset expressed in sanitized CHARACTERS (3*5 + 2 + 2 + 16 = 35) would
        # point past both lines: with it, nothing is proven and the scan still fires.
        char_offset = 3 * len("<ESC>") + 2 + 2 + len(refusal)
        res = self._resolve(raw, self._ev("login required\n", char_offset, _pty()))
        self.assertEqual(res["reason"], "event[0]:echo_not_found")
        self.assertTrue(self._refusals(raw, self._ev("login required\n", char_offset, _pty())))
        # A negative (pre-window) offset is never clamped: unproven by name.
        res = self._resolve(raw, self._ev("login required\n", -1, _pty()))
        self.assertEqual(res["reason"], "event[0]:delivery_before_window")

    # -- 8. a genuine refusal immediately AFTER the proven echo --------------------------
    def test_a_genuine_refusal_immediately_after_the_proven_echo_fires(self) -> None:
        echo = self.PAYLOAD.replace("\n", "\r\n").encode()
        ev = self._ev(self.PAYLOAD, 0, _pty())
        for tail in (b"login required", b"\r\nlogin required\r\n",
                     b"You are not logged in.", b"\r\nAllow this tool to run?"):
            with self.subTest(tail=tail):
                raw = echo + tail
                res = self._resolve(raw, ev)
                self.assertEqual(res["state"], "echo_proven")
                self.assertEqual(res["spans"], ((0, len(echo)),))
                self.assertEqual(lifecycle.strip_delivery_echo(raw, (ev,)), tail)
                self.assertTrue(self._refusals(raw, ev), f"{tail!r} must fire after the echo")

    # -- structured-first refusal evidence -----------------------------------------------
    def _claude_driver(self):
        base = Path(tempfile.mkdtemp(prefix="os37-b1drv-"))
        self.addCleanup(shutil.rmtree, base, True)
        (base / "wt").mkdir()
        spec = stub_profile_spec("alive", worktree=str(base / "wt"))
        spec["auth_markers"] = [["error", "authentication_failed"],
                                ["is_api_error_message", "True"],
                                ["terminal_reason", "api_error"]]
        return drivers_mod.driver_for(profile_from_mapping(spec))

    def test_structured_records_are_consulted_first_and_free_text_is_the_fallback(self) -> None:
        driver = self._claude_driver()
        events = ({"offset": 0, "payload": "Task: login required\nContinue",
                   "transport": ARGV_TRANSPORT},)
        # A tool result / assistant message QUOTING the task text is that record's content,
        # not a terminal gate: no refusal (the real F5 capture shape).
        quoted = (b'{"type":"system","subtype":"init","session_id":"s1"}\r\n'
                  b'{"type":"user","message":{"content":[{"type":"tool_result",'
                  b'"content":"# WORKER.md\\nlogin required to continue"}]}}\r\n'
                  b'{"type":"assistant","message":{"content":[{"type":"text",'
                  b'"text":"The task says login required; proceeding."}]}}\r\n')
        ev = driver.readiness_evidence(quoted.decode(), minted_session_id="s1",
                                       liveness=None, raw=quoted, delivery_events=events)
        self.assertEqual(tuple(ev["refusals"]), ())
        self.assertEqual(ev["refusal_source"], "none")
        self.assertEqual(ev["echo"]["state"], "echo_absent")
        # A DECLARED structured marker fires with structured provenance, whatever the prose.
        marked = quoted + (b'{"type":"assistant","session_id":"s1",'
                           b'"error":"authentication_failed","message":{}}\r\n')
        ev = driver.readiness_evidence(marked.decode(), minted_session_id="s1",
                                       liveness=None, raw=marked, delivery_events=events)
        self.assertEqual(tuple(ev["refusals"]), ("blocked_prompt_beats_idle",))
        self.assertEqual(ev["refusal_source"], "structured")
        self.assertEqual(ev["structured_hit"]["marker"]["field"], "error")
        # Free text that is NOT a structured record is the fallback: a prose login gate fires.
        prose = quoted + b"Please log in to continue.\r\n"
        ev = driver.readiness_evidence(prose.decode(), minted_session_id="s1",
                                       liveness=None, raw=prose, delivery_events=events)
        self.assertEqual(tuple(ev["refusals"]), ("blocked_prompt_beats_idle",))
        self.assertEqual(ev["refusal_source"], "free_text")
        # And a text-only caller gets the same structured-first partition.
        ev = driver.readiness_evidence(quoted.decode(), minted_session_id="s1", liveness=None)
        self.assertEqual(tuple(ev["refusals"]), ())
        self.assertTrue(driver.readiness_evidence(prose.decode(), minted_session_id="s1",
                                                  liveness=None)["refusals"])

    # -- fail closed BY NAME through S3 ---------------------------------------------------
    def test_an_unproven_echo_makes_readiness_unprovable_and_named_never_ready(self) -> None:
        base = {"liveness": _LIVE, "bound_signal": _bound(), "refusals": (),
                "supplementary": ()}
        for reason, echo in (
                ("event[0]:transport_unrecorded", {"state": "echo_unproven"}),
                ("event[0]:echo_not_found", {"state": "echo_unproven"}),
                ("event[0]:ambiguous_multiple_matches", {"state": "echo_unproven"})):
            with self.subTest(reason=reason):
                verdict = lifecycle.may_send_prompt(
                    {**base, "echo": {**echo, "reason": reason}},
                    minted_session_id=_MINTED, declared_record_types=_DECLARED)
                self.assertEqual(verdict["verdict"], "unprovable")
                self.assertEqual(verdict["reason"], f"echo_unproven:{reason}")
                self.assertTrue(verdict["quorum"]["R-C"], "no refusal fired -- and still not ready")
                # And the deadline verdict CARRIES it, so a timeout stays named.
                expired = lifecycle.may_send_prompt(
                    {**base, "echo": {**echo, "reason": reason}},
                    minted_session_id=_MINTED, declared_record_types=_DECLARED,
                    deadline_expired=True)
                self.assertEqual(expired["reason"], "deadline_expired")
                self.assertEqual(expired["expired_on"]["reason"], f"echo_unproven:{reason}")
        # Proven / absent echoes do not block a closed quorum.
        for state in ("echo_proven", "echo_absent", "no_delivery"):
            with self.subTest(state=state):
                verdict = lifecycle.may_send_prompt(
                    {**base, "echo": {"state": state, "reason": ""}},
                    minted_session_id=_MINTED, declared_record_types=_DECLARED)
                self.assertEqual(verdict["verdict"], "ready")
        # A fired refusal keeps precedence over an unproven echo (both block).
        verdict = lifecycle.may_send_prompt(
            {**base, "refusals": ("blocked_prompt_beats_idle",),
             "echo": {"state": "echo_unproven", "reason": "event[0]:echo_not_found"}},
            minted_session_id=_MINTED, declared_record_types=_DECLARED)
        self.assertEqual(verdict["verdict"], "not_ready")

    def test_the_driver_reports_an_unproven_echo_end_to_end_through_s3(self) -> None:
        driver = self._claude_driver()
        raw = self.PAYLOAD.encode() + b"\r\n"                 # LF echo under an ONLCR transport
        events = ({"offset": 0, "payload": self.PAYLOAD, "transport": _pty(onlcr=True)},)
        ev = driver.readiness_evidence(raw.decode(), minted_session_id="s1", liveness=_LIVE,
                                       raw=raw, delivery_events=events)
        self.assertEqual(ev["echo"]["state"], "echo_unproven")
        self.assertEqual(ev["echo"]["reason"], "event[0]:echo_not_found")
        self.assertTrue(ev["refusals"], "the superset scan fires on the runtime's own text")
        verdict = lifecycle.may_send_prompt(
            {**ev, "bound_signal": _bound()}, minted_session_id=_MINTED,
            declared_record_types=_DECLARED)
        self.assertNotEqual(verdict["verdict"], "ready")
        # The proven counterpart reaches ready on the same quorum.
        proven = self.PAYLOAD.replace("\n", "\r\n").encode() + b"\r\n"
        ev = driver.readiness_evidence(proven.decode(), minted_session_id="s1", liveness=_LIVE,
                                       raw=proven, delivery_events=events)
        self.assertEqual(ev["echo"]["state"], "echo_proven")
        self.assertEqual(tuple(ev["refusals"]), ())
        self.assertEqual(lifecycle.may_send_prompt(
            {**ev, "bound_signal": _bound()}, minted_session_id=_MINTED,
            declared_record_types=_DECLARED)["verdict"], "ready")

    # -- the transport is READ from a real pty, and the derived forms match its real echo --
    def test_a_real_pty_echo_equals_a_derived_form_under_its_read_termios(self) -> None:
        import pty as _pty_mod
        import select
        import termios as _termios
        from scripts.deterministic_workflow import standalone_pty as pty_supervisor

        def drain(fd: int, budget_s: float = 0.5) -> bytes:
            out, deadline = b"", time.monotonic() + budget_s
            while time.monotonic() < deadline:
                ready, _, _ = select.select([fd], [], [], 0.05)
                if not ready:
                    if out:
                        break
                    continue
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            return out

        payload = "Task contract:\nlogin required\tnow\x01\nContinue ☕"
        cases = {"echo-off": lambda t: (t[3] & ~(_termios.ECHO | _termios.ICANON), t[1]),
                 "echo-onlcr": lambda t: ((t[3] | _termios.ECHO) & ~_termios.ICANON, t[1]),
                 "echo-no-onlcr": lambda t: ((t[3] | _termios.ECHO) & ~_termios.ICANON,
                                             t[1] & ~_termios.ONLCR),
                 "echo-tabs": lambda t: ((t[3] | _termios.ECHO) & ~_termios.ICANON,
                                         t[1] | getattr(_termios, "TABDLY", 0) | 0x4),
                 "echo-canonical": lambda t: (t[3] | _termios.ECHO | _termios.ICANON, t[1])}
        for name, mutate in cases.items():
            with self.subTest(case=name):
                master, slave = _pty_mod.openpty()
                self.addCleanup(os.close, master)
                self.addCleanup(os.close, slave)
                mode = _termios.tcgetattr(slave)
                mode[3], mode[1] = mutate(mode)
                _termios.tcsetattr(slave, _termios.TCSANOW, mode)
                # The transport is read on the MASTER, at delivery time, as the runtime does.
                transport = pty_supervisor.echo_transport(master, kind="pty_write",
                                                          framed=True, cols=120)
                self.assertIsNotNone(transport["termios"], "tcgetattr on the master")
                for flag in lifecycle.TERMIOS_ECHO_FLAGS:
                    self.assertIn(flag, transport["termios"])
                self.assertEqual(transport["termios"]["echo"], name != "echo-off")
                # Write exactly what `drivers.deliver` writes: the frame, then Enter.
                frame = drivers_mod.frame_prompt(payload)
                os.write(master, frame)
                echoed = drain(master)
                os.write(master, b"\r")
                trailer = drain(master, 0.3)
                os.read(slave, 65536)                              # the "agent" consumes it
                events = ({"offset": 0, "payload": payload, "transport": transport},)
                res = lifecycle.resolve_delivery_echo(echoed + trailer + b"ok\r\n", events)
                if name == "echo-off":
                    self.assertEqual(echoed, b"", "ECHO clear: the pty echoed nothing")
                    self.assertEqual(res["state"], "echo_absent", res)
                    continue
                derived = lifecycle.expected_echo_forms(payload, transport)
                self.assertEqual(derived["state"], "echo_expected", derived)
                self.assertIn(echoed, derived["forms"],
                              f"{name}: the real echo {echoed!r} is not among the derived "
                              f"forms {derived['forms']!r}")
                self.assertEqual(res["state"], "echo_proven", res)
                self.assertEqual(res["spans"], ((0, len(echoed)),))
                self.assertEqual(lifecycle.classify_refusals_in_capture(
                    echoed + trailer + b"ok\r\n", events), (),
                    f"{name}: the runtime's own echoed task text fired as a refusal")
                self.assertTrue(lifecycle.classify_refusals_in_capture(
                    echoed + trailer + b"You are not logged in.\r\n", events),
                    f"{name}: a genuine refusal after the real echo must fire")

    def test_termios_evidence_is_none_not_a_default_when_unreadable(self) -> None:
        from scripts.deterministic_workflow import standalone_pty as pty_supervisor
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        self.addCleanup(os.close, w)
        self.assertIsNone(pty_supervisor.termios_evidence(r), "a pipe has no termios")
        transport = pty_supervisor.echo_transport(r, kind="pty_write", framed=True, cols=80)
        self.assertIsNone(transport["termios"])
        res = lifecycle.resolve_delivery_echo(
            b"login required", ({"offset": 0, "payload": "login required",
                                 "transport": transport},))
        self.assertEqual((res["state"], res["reason"]),
                         ("echo_unproven", "event[0]:termios_unreadable"))
        self.assertEqual(pty_supervisor.echo_transport(None, kind="argv", framed=False,
                                                       cols=80)["kind"], "argv")


# =====================================================================================
# B1 (iteration 3, CI) -- the LINUX line discipline is a first-class transport variant
# =====================================================================================
class B1LinuxLineDisciplineIsAFirstClassTransportTests(unittest.TestCase):
    """GitHub Actions run 34821771744 on `a4c8c5f` (ubuntu, all six jobs) failed three
    subtests of the real-pty lock: the derived forms encoded the BSD/macOS line discipline
    only.  Linux `n_tty` in NON-canonical mode renders a literal NL as `^J` under `ECHOCTL`
    (BSD exempts TAB and NL; Linux exempts TAB only), emits no `\\r\\n`/`\\n`, and the output
    column does NOT reset -- which also shifts `XTABS` expansion (3 spaces where BSD gives 2).

    The transport evidence now records WHICH discipline the flags were read from
    (`termios.platform`, `termios.discipline` in `standalone_pty.termios_evidence`), and
    `expected_echo_forms` derives per discipline -- never from a "both kernels" guess.  An
    evidence block without a known discipline is `echo_unproven:transport_unrecorded`.

    These locks REPLAY the exact Linux echo bytes captured in the CI logs
    (`evidence/CI_UBUNTU_a4c8c5f/job_*.log`) under a Linux transport, so the Linux behaviour
    is proven on this host too; the unchanged real-pty lock proves the BSD side live.  Red at
    `a4c8c5f` (`evidence/iter3/B1_linux_RED_at_a4c8c5f.txt`)."""

    PAYLOAD = "Task contract:\nlogin required\tnow\x01\nContinue ☕"   # the real-pty payload
    # The bytes six ubuntu jobs captured from a real Linux pty for that payload, framed:
    CI_ONLCR = b"^[[200~Task contract:^Jlogin required\tnow^A^JContinue \xe2\x98\x95^[[201~"
    CI_NO_ONLCR = CI_ONLCR                                        # identical: no NL is emitted
    CI_TABS = b"^[[200~Task contract:^Jlogin required   now^A^JContinue \xe2\x98\x95^[[201~"
    # `echo-canonical` PASSED on every ubuntu job against the single BSD-derived form, so the
    # Linux canonical echo is that form: NL echoed raw through OPOST/ONLCR.
    CI_CANONICAL = (b"^[[200~Task contract:\r\nlogin required\tnow^A\r\nContinue "
                    b"\xe2\x98\x95^[[201~")

    def _forms(self, transport: dict, payload: str | None = None) -> tuple:
        derived = lifecycle.expected_echo_forms(payload or self.PAYLOAD, transport)
        self.assertEqual(derived["state"], "echo_expected", derived)
        return derived["forms"]

    def _proven(self, raw: bytes, transport: dict, payload: str | None = None) -> None:
        events = ({"offset": 0, "payload": payload or self.PAYLOAD, "transport": transport},)
        res = lifecycle.resolve_delivery_echo(raw + b"\r\nok\r\n", events)
        self.assertEqual(res["state"], "echo_proven", res)
        self.assertEqual(res["spans"], ((0, len(raw)),))
        self.assertEqual(lifecycle.classify_refusals_in_capture(raw + b"\r\nok\r\n", events),
                         (), "the runtime's own echoed task text fired as a refusal")
        self.assertTrue(lifecycle.classify_refusals_in_capture(
            raw + b"\r\nYou are not logged in.\r\n", events),
            "a genuine refusal after the proven Linux echo must fire")

    def test_ci_case_echo_onlcr_linux_renders_a_raw_mode_nl_as_ctrl_j(self) -> None:
        t = _linux(framed=True, onlcr=True)
        self.assertEqual(self._forms(t), (self.CI_ONLCR,))
        self._proven(self.CI_ONLCR, t)

    def test_ci_case_echo_no_onlcr_is_the_same_bytes_because_no_nl_is_emitted(self) -> None:
        t = _linux(framed=True, onlcr=False)
        self.assertEqual(self._forms(t), (self.CI_NO_ONLCR,))
        self._proven(self.CI_NO_ONLCR, t)

    def test_ci_case_echo_tabs_counts_ctrl_j_as_two_columns_with_no_reset(self) -> None:
        t = _linux(framed=True, onlcr=True, tab_expand=True)
        forms = self._forms(t)
        self.assertEqual(len(forms), 8, "one form per unobservable start column")
        self.assertIn(self.CI_TABS, forms)               # start column 0: col 37 -> 3 spaces
        self._proven(self.CI_TABS, t)
        # The BSD derivation for the same flags is NOT a Linux form (2 spaces: NL reset it).
        bsd = lifecycle.expected_echo_forms(self.PAYLOAD, _pty(framed=True, onlcr=True,
                                                               tab_expand=True))["forms"]
        self.assertNotIn(self.CI_TABS, bsd)
        self.assertIn(b"^[[200~Task contract:\r\nlogin required  now^A\r\nContinue "
                      b"\xe2\x98\x95^[[201~", bsd)

    def test_ci_case_echo_canonical_linux_echoes_nl_raw_through_opost(self) -> None:
        t = _linux(framed=True, onlcr=True, icanon=True)
        self.assertEqual(self._forms(t), (self.CI_CANONICAL,))
        self._proven(self.CI_CANONICAL, t)

    def test_the_same_flags_derive_different_forms_per_discipline_never_both(self) -> None:
        # ONE transport -> ONE discipline -> forms for THAT kernel only.  The former "both
        # kernels" enumeration is gone: a BSD transport never carries a Linux form and vice
        # versa, so a form can never be derived for the wrong kernel.
        linux = self._forms(_linux(framed=True, onlcr=True))
        bsd = self._forms(_pty(framed=True, onlcr=True))
        self.assertEqual(linux, (self.CI_ONLCR,))
        self.assertEqual(bsd, (self.CI_CANONICAL,))          # BSD raw-mode NL -> CRLF
        self.assertFalse(set(linux) & set(bsd))
        # Column accounting after NL differs per discipline under XTABS, ONLCR clear.
        self.assertIn(b"a\n        b", self._forms(_pty(tab_expand=True, onlcr=False), "a\n\tb"))
        self.assertEqual(self._forms(_linux(tab_expand=True, onlcr=False), "a\n\tb"),
                         tuple(b"a^J" + b" " * (8 - ((c + 3) % 8)) + b"b" for c in range(8)))

    def test_linux_cr_under_icrnl_is_echoed_raw_but_a_literal_nl_is_ctrl_j(self) -> None:
        # n_tty: a CR that ICRNL turns into NL reaches `echo_char_raw` (OPOST: ONLCR -> CRLF,
        # column reset); a literal NL byte is not a special char in raw mode and reaches
        # `echo_char` (ECHOCTL -> ^J, column += 2).  Both from the kernel source; the NL leg
        # is the one CI measured.
        self.assertEqual(self._forms(_linux(onlcr=True), "a\rb\nc"), (b"a\r\nb^Jc",))
        self.assertEqual(self._forms(_linux(onlcr=False), "a\rb\nc"), (b"a\nb^Jc",))
        self.assertEqual(self._forms(_linux(onlcr=True, echoctl=False), "a\nb"), (b"a\r\nb",))
        self.assertEqual(self._forms(_linux(onlcr=True, icrnl=False), "a\rb"), (b"a^Mb",))
        self.assertEqual(self._forms(_linux(opost=False), "a\nb"), (b"a^Jb",))

    def test_echonl_without_echo_is_a_partial_echo_named_unproven_on_both_disciplines(self) -> None:
        for t in (_linux(echo=False, echonl=True, icanon=True),
                  _pty(echo=False, echonl=True, icanon=True)):
            with self.subTest(discipline=t["termios"]["discipline"]):
                derived = lifecycle.expected_echo_forms("a\nb", t)
                self.assertEqual((derived["state"], derived["reason"]),
                                 ("echo_unproven", "echonl_partial_echo"))
                self.assertIn("echonl_partial_echo", lifecycle.ECHO_UNPROVEN_REASONS)
        # ECHONL with no NL in the payload, ECHO clear: nothing can echo -> absent.
        self.assertEqual(lifecycle.expected_echo_forms(
            "ab", _linux(echo=False, echonl=True, icanon=True))["state"], "echo_absent")

    def test_an_unknown_or_missing_discipline_is_transport_unrecorded(self) -> None:
        for bad in ({"discipline": None}, {"discipline": "solaris_ldterm"},
                    {"discipline": ""}):
            with self.subTest(bad=bad):
                t = _pty(**bad)
                derived = lifecycle.expected_echo_forms(self.PAYLOAD, t)
                self.assertEqual((derived["state"], derived["reason"]),
                                 ("echo_unproven", "transport_unrecorded"))
                res = lifecycle.resolve_delivery_echo(
                    self.CI_CANONICAL, ({"offset": 0, "payload": self.PAYLOAD,
                                         "transport": t},))
                self.assertEqual(res["reason"], "event[0]:transport_unrecorded")
        t = _pty()
        del t["termios"]["discipline"]
        self.assertEqual(lifecycle.expected_echo_forms(self.PAYLOAD, t)["reason"],
                         "transport_unrecorded")

    def test_termios_evidence_records_the_discipline_of_this_host(self) -> None:
        import pty as _pty_mod
        from scripts.deterministic_workflow import standalone_pty as pty_supervisor
        master, slave = _pty_mod.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        flags = pty_supervisor.termios_evidence(master)
        self.assertIsNotNone(flags)
        self.assertEqual(flags["platform"], os.uname().sysname)
        expected = {"Linux": "linux_n_tty", "Darwin": "bsd_ttydisc"}.get(os.uname().sysname)
        self.assertEqual(flags["discipline"], expected)
        self.assertIn(flags["discipline"], lifecycle.LINE_DISCIPLINES)
        self.assertIn("echonl", flags)


# =====================================================================================
# B1'' -- (iteration-2 review) overlapping candidate spans are AMBIGUOUS, never collapsed
# =====================================================================================
class B1OverlappingCandidateSpansAreAmbiguousTests(unittest.TestCase):
    """Iteration-1 review B1: `_occurrences` advanced its cursor by `len(form)` after a hit,
    so a second candidate starting INSIDE the first was never seen -- payload `aaa` in raw
    `aaaa` resolved `echo_proven` span `(0, 3)` and excised it, although `(1, 4)` is equally
    supported by the evidence.  Ambiguity is now decided by SPAN IDENTITY across every start
    position of every derived form: two distinct forms mapping to the same raw span are ONE
    candidate (proven); any two distinct spans are `echo_unproven:ambiguous_multiple_matches`
    with NO excision.  Red on the iteration-1 staged tree
    (`evidence/iter2/B1_overlap_RED_at_iter1_tree.txt`)."""

    @staticmethod
    def _ev(payload: str, transport: dict | None = None, offset: int = 0) -> tuple[dict, ...]:
        return ({"offset": offset, "payload": payload,
                 "transport": transport if transport is not None else _pty(onlcr=False)},)

    def _resolve(self, raw: bytes, payload: str, **kw: Any) -> dict:
        return lifecycle.resolve_delivery_echo(raw, self._ev(payload, **kw))

    def _assert_ambiguous(self, raw: bytes, payload: str, **kw: Any) -> None:
        res = self._resolve(raw, payload, **kw)
        self.assertEqual((res["state"], res["reason"]),
                         ("echo_unproven", "event[0]:ambiguous_multiple_matches"), res)
        self.assertEqual(res["spans"], ())
        self.assertEqual(lifecycle.strip_delivery_echo(raw, self._ev(payload, **kw)), raw,
                         "an ambiguous echo must excise NOTHING")

    def test_the_reviewers_aaa_in_aaaa_attack_is_ambiguous_not_proven(self) -> None:
        self._assert_ambiguous(b"aaaa", "aaa")
        # The exact-length control is the single candidate and IS proven.
        res = self._resolve(b"aaa", "aaa")
        self.assertEqual((res["state"], res["spans"]), ("echo_proven", ((0, 3),)))

    def test_overlap_where_the_payload_carries_a_blocking_phrase_fires(self) -> None:
        payload = "login required login required"
        raw = b"login required login required login required"
        self._assert_ambiguous(raw, payload)                 # spans (0,29) and (15,44)
        self.assertTrue(lifecycle.classify_refusals_in_capture(raw, self._ev(payload)),
                        "nothing is excised, so the runtime's own phrase FIRES (fail closed)")
        # The unambiguous control: exactly the payload, once -> proven, nothing fires.
        self.assertEqual(self._resolve(payload.encode(), payload)["state"], "echo_proven")
        self.assertEqual(lifecycle.classify_refusals_in_capture(payload.encode(),
                                                                self._ev(payload)), ())

    def test_distinct_derived_forms_on_the_same_raw_span_are_one_candidate(self) -> None:
        # `_occurrences` fed two DISTINCT form slots that both equal the raw window: the same
        # span twice is ONE candidate.
        hits = lifecycle._occurrences(b"xx", (b"xx", b"xx"), 0, 2)
        self.assertEqual(hits, [(0, 2)], "identical spans from distinct form slots dedupe")
        # And through the public model: tab expansion enumerates one form per unobservable
        # start column (8 distinct forms for `ab\tc`); only ONE of them is in the capture, so
        # exactly one distinct span exists -> proven, whichever column it was.
        transport = _pty(tab_expand=True)
        derived = lifecycle.expected_echo_forms("ab\tc", transport)
        self.assertEqual(len(derived["forms"]), 8)
        for form in derived["forms"]:
            with self.subTest(form=form):
                res = self._resolve(b"<" + form + b">", "ab\tc", transport=transport,
                                    offset=0)
                self.assertEqual(res["state"], "echo_proven", res)
                self.assertEqual(res["spans"], ((1, 1 + len(form)),))

    def test_distinct_derived_forms_on_different_spans_are_ambiguous(self) -> None:
        transport = _pty(tab_expand=True)
        derived = lifecycle.expected_echo_forms("ab\tc", transport)
        two = derived["forms"][:2]
        self.assertNotEqual(two[0], two[1])
        raw = two[0] + b"\r\n" + two[1]                       # each form once, different spans
        self._assert_ambiguous(raw, "ab\tc", transport=transport)

    def test_property_self_overlapping_payloads_are_proven_only_with_exactly_one_span(self) -> None:
        rng = random.Random(20260915)
        alphabet = ("a", "ab", "aba", "login required", "\n", " ", "x")
        for _ in range(400):
            unit = rng.choice(alphabet[:3]) if rng.random() < 0.7 else rng.choice(alphabet)
            payload = unit * rng.randint(1, 4)
            form = payload.encode()
            # A raw window that SELF-OVERLAPS: the payload followed by one of its own
            # suffixes (a shifted partial copy), optionally preceded by one of its prefixes
            # and padded -- the shapes a cursor that skips `len(form)` cannot see.
            j = rng.randint(0, len(form))
            head = form[:rng.randint(0, len(form))] if rng.random() < 0.5 else b""
            pad = rng.choice((b"", b"\n", b"|", b"zz"))
            raw = pad + head + form + form[j:] + pad
            events = self._ev(payload)
            res = lifecycle.resolve_delivery_echo(raw, events)
            # Ground truth by exhaustive enumeration of every start position.
            spans = {(i, i + len(form)) for i in range(len(raw) - len(form) + 1)
                     if raw[i:i + len(form)] == form}
            if len(spans) == 1:
                self.assertEqual(res["state"], "echo_proven", (payload, raw, res))
                self.assertEqual(res["spans"], (next(iter(spans)),))
            else:
                self.assertEqual(res["state"], "echo_unproven", (payload, raw, res))
                self.assertEqual(res["reason"], "event[0]:ambiguous_multiple_matches")
                self.assertEqual(lifecycle.strip_delivery_echo(raw, events), raw)


# =====================================================================================
# B1' -- the RUNTIME records the transport at the write and journals the echo verdict
# =====================================================================================
class B1RuntimeRecordsTransportAtDeliveryTests(_Composed):
    """`StandaloneSession.send` records the `EchoTransport` (kind, framing, live termios
    read on the master at the write) in the delivery event; `_verify_delivery` partitions
    the post-write bytes by it; the delivery journal row carries the echo verdict and the
    transport, so an unproven echo is visible in the settlement.  Red on the staged tree:
    the event has no `transport` key and the row has no `echo` vocabulary."""

    def test_send_records_the_live_transport_and_journals_the_echo_verdict(self) -> None:
        run_id = "run_b1rt"
        # Round-7 item 6: the stub answers with the record the driver's conjunctive
        # delivery selector accepts; a turn start alone no longer confirms a delivery.
        spec = stub_profile_spec("deliver-claude-proof", worktree=self.worktree,
                                 timeouts={"delivery_verify_timeout_ms": 6000})
        adapter, _state, ledger = self.compose_spec(spec, run_id=run_id)
        intent = {**WORKER_INTENT_KEYS, "intent_id": "i-b1rt", "run_id": run_id,
                  "role": "WORKER"}
        claim = ledger.claim(intent)
        session = adapter.runtime.session_for(intent)
        receipt = session.start(lease_token=claim["lease_token"], **INJECTED_REHEARSALS)
        self.assertEqual(receipt["start_outcome"], "ready", receipt)
        payload = "Task contract:\nlogin required\nContinue"
        result = session.send({"payload": payload})
        self.assertEqual(result["delivery"], "delivered_confirmed", result)
        self.assertEqual(result["proof"], "agent_response")
        # The event carries the transport read at the write: a framed pty write into the
        # raw-mode slave this runtime configured (`_set_raw` clears ECHO).
        event = session.delivery_events[-1]
        self.assertEqual(event["payload"], payload)
        transport = event["transport"]
        self.assertEqual((transport["kind"], transport["framed"]), ("pty_write", True))
        self.assertIsNotNone(transport["termios"], "the master's termios was readable")
        self.assertFalse(transport["termios"]["echo"])
        for flag in lifecycle.TERMIOS_ECHO_FLAGS:
            self.assertIn(flag, transport["termios"])
        # The verifier partitioned by it: no echo possible -> nothing excluded, nothing
        # unproven -- and the task text carried no refusal because nothing echoed it.
        self.assertEqual(result["echo"]["state"], "echo_absent", result["echo"])
        self.assertNotIn(b"login required", session.capture.raw(),
                         "a raw-mode slave echoes nothing back into the capture")
        # The journal row names the verdict and the transport.
        rows = [r for r in self.journal(run_id).rows_for("i-b1rt")
                if r["event"] == "delivery_proof_observed"]
        self.assertEqual(len(rows), 1)
        vocab = rows[0]["source_vocabulary"]
        self.assertEqual(vocab["echo"]["state"], "echo_absent")
        self.assertEqual(vocab["echo"]["events"][0]["reason"], "echo_flag_clear")
        self.assertEqual(vocab["transport"]["kind"], "pty_write")
        self.assertFalse(vocab["transport"]["termios"]["echo"])
        self.assertEqual(vocab["refusals"], [])
        json.dumps(vocab)                                     # journal-safe throughout


# =====================================================================================
# B2 -- adopting a retained LOST dispatch preserves its lost_reason (typed, once, no crash)
# =====================================================================================
class B2LostAdoptionPreservesReasonTests(_Composed):
    """[P1] `adopt` restored the retained row's STATE but not its `lost_reason`, so the
    adoption's journal write of `state=LOST` carried an empty reason and `make_record`
    raised `ValueError` -- a traceback escaped instead of a typed blocked/unsettled result.
    Red at `87f6179`: the resume below raises `ValueError`."""

    def _alive_spec(self) -> dict:
        return stub_profile_spec(
            "alive", worktree=self.worktree,
            timeouts={"graceful_force_timeout_ms": 500, "force_retry_ms": 20,
                      "physical_exit_timeout_ms": 1500, "staleness_budget_ms": 5000})

    def test_a_retained_lost_row_is_adopted_typed_with_its_exact_reason_once(self) -> None:
        run_id = "run_b2lost"
        ledger = FileRuntimeStateStore(self.base / "b2.json")
        adapter, _state, ledger = self.compose_spec(self._alive_spec(), run_id=run_id,
                                                    ledger=ledger)
        intent = {**WORKER_INTENT_KEYS, "intent_id": "i-b2", "run_id": run_id,
                  "role": "WORKER"}
        claim = ledger.claim(intent)
        session = adapter.runtime.session_for(intent)
        session.start(lease_token=claim["lease_token"], **INJECTED_REHEARSALS)
        # A programming error escaping over the (now exited) child RETAINS the dispatch as
        # a typed LOST with an in-vocabulary reason -- the exact scenario B2/F4 name.
        session.secure_after_unexpected(RuntimeError("an unnamed programming error"))
        retained = [row for row in self.journal(run_id).rows_for("i-b2")
                    if row.get("state") == "LOST"]
        self.assertTrue(retained, "no retained LOST row was written")
        original_reason = retained[-1]["lost_reason"]
        self.assertEqual(original_reason, "evidence_unreadable")
        self.assertIn("i-b2", adapter.open_dispatches(),
                      "the retained dispatch must stay OPEN before adoption")
        stored = ledger.get_receipt("i-b2")
        fence = stored["receipt"]["external_id"]
        # A STRANGER composition (the Watchdog / resume path) adopts the retained dispatch.
        launcher.publish_standalone_launch_bindings(
            self.base, run_id, profile_spec=self._alive_spec(),
            runtime_state_path=ledger.path, thread_id="t")
        stranger, _j, _p = launcher.standalone_recovery_composition(
            self.base, run_id, thread_id="t",
            ledger=FileRuntimeStateStore(self.base / "b2.json"),
            pause_row_journal=launcher._standalone_pause_row_journal(self.base, run_id))
        escaped = None
        try:
            event = stranger.resume(intent, dict(stored["receipt"]))
        except BaseException as exc:            # noqa: BLE001 - the ESCAPE is the finding
            escaped = exc
        self.assertIsNone(escaped, f"adoption escaped as {escaped!r} (base: ValueError)")
        # The adoption's own journal write preserves the EXACT original reason (B2).
        adopted = [row for row in self.journal(run_id).rows_for("i-b2")
                   if (row.get("source_vocabulary") or {}).get("adopted") is True
                   and row["event"] == "identity_bound"]
        self.assertEqual(len(adopted), 1, "the successor did not reconstruct the session")
        self.assertEqual(adopted[0]["state"], "LOST")
        self.assertEqual(adopted[0]["lost_reason"], original_reason,
                         "the adoption dropped or changed the retained LOST reason")
        # Exactly-once settlement, as a typed BLOCKED outcome -- never a success.
        settled = [row for row in self.journal(run_id).rows_for("i-b2")
                   if row["kind"] == "SETTLEMENT_OBSERVED"]
        self.assertEqual(len(settled), 1, "the adoption did not settle exactly once")
        self.assertEqual(f"{settled[0]['session_id']}:{settled[0]['process_incarnation']}",
                         fence, "the settlement is not fenced to the retained incarnation")
        self.assertEqual((event.get("result") or {}).get("status"), "BLOCKED",
                         "a retained LOST adoption must settle a typed BLOCKED outcome")
        self.assertNotEqual(settled[0]["outcome"], "succeeded")


# =====================================================================================
# B5 / B2' -- the recovery-path refusal matrix: 6 corruptions × 5 routes, on a RECOVERABLE
#             run, each asserting the SPECIFIC authority code at the boundary + untouched.
# =====================================================================================
@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class B5RecoveryPathRefusalMatrixTests(unittest.TestCase):
    """B5, made real (iteration-3 finding B2').

    A REAL, RECOVERABLE paused standalone run (a committed head + a recorded authority,
    thread ``graph``) has its authority file corrupted six ways, and EVERY recovery route
    -- ``resume``, ``resume --cancel``, ``resume --abandon``, ``recover`` and ``watchdog
    once`` -- is asserted to refuse with the SPECIFIC typed authority error
    (`STANDALONE_ADAPTER_REQUIRES_LEDGER`), at the authority boundary (NO downstream
    ``RECOVERY_*`` code in the output), leaving the authority bytes byte-identical.

    Because the fixture is genuinely recoverable, a downstream error (e.g. the old
    `RECOVERY_HEAD_MISSING` / `RECOVERY_GRAPH_UNAVAILABLE`) can no longer masquerade as the
    refusal -- and this is exactly what catches B1' on the recover/watchdog column: at the
    iteration-2 code those two routes loaded the tampered authority and returned a
    ``RECOVERY_*`` code, so the "authority code, not RECOVERY_*" assertion is RED there.
    """

    RUN = "run_b5mtx"
    THREAD = "graph"

    def setUp(self) -> None:
        from scripts.test_os37_external_review_regressions import (GRAPH_CREDENTIAL_ENV,
                                                                   GRAPH_CREDENTIAL_VALUE,
                                                                   execute_graph_cli)
        from scripts.test_os37_pause_production_path import (REASON_CODE,
                                                             _declare_blocked_source,
                                                             _publish_open_decision)
        self.room = Path(tempfile.mkdtemp(prefix="os37-b5mtx-"))
        self.addCleanup(shutil.rmtree, self.room, True)
        self.previous_dir = os.environ.get(launcher.RUNTIME_STATE_DIR_ENV)
        os.environ[launcher.RUNTIME_STATE_DIR_ENV] = str(self.room / "default-ledgers")
        self.addCleanup(self._restore_dir)
        self.base = self.room / "artifact_base"
        self.base.mkdir(parents=True)
        key = _publish_open_decision(self.RUN, self.base)
        _declare_blocked_source(self.RUN, self.base, key)
        paused = execute_graph_cli(self.room, run_id=self.RUN,
                                   approval_authority="artifact",
                                   decision_state="NEEDS_INPUT",
                                   decision_reason_code=REASON_CODE)
        self.assertIsNone(paused.escaped)
        self.assertEqual(paused.summary.get("run_lifecycle"), "WAITING_FOR_INPUT",
                         f"{paused.summary!r}\n{paused.stderr}")
        self.auth_path = launcher.standalone_authority_path(self.base, self.RUN, self.THREAD)
        self.assertTrue(self.auth_path.exists(), "the paused run recorded no authority")
        self.valid = json.loads(self.auth_path.read_text())
        # The run is genuinely recoverable and its recorded thread is the launch thread.
        # (Read from the record directly, NOT via a helper introduced in the fix, so this
        # matrix runs -- and its recover/watchdog column FAILS -- against the iteration-2
        # tree, which is how it catches B1'.)
        self.assertEqual(self.valid.get("thread_id"), self.THREAD)
        self.previous_cred = os.environ.get(GRAPH_CREDENTIAL_ENV)
        os.environ[GRAPH_CREDENTIAL_ENV] = GRAPH_CREDENTIAL_VALUE
        self.addCleanup(self._restore_cred, GRAPH_CREDENTIAL_ENV)

    def _restore_dir(self) -> None:
        if self.previous_dir is None:
            os.environ.pop(launcher.RUNTIME_STATE_DIR_ENV, None)
        else:
            os.environ[launcher.RUNTIME_STATE_DIR_ENV] = self.previous_dir

    def _restore_cred(self, name: str) -> None:
        if self.previous_cred is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = self.previous_cred

    def _corruptions(self) -> dict[str, bytes]:
        good = json.dumps(self.valid, sort_keys=True, indent=2) + "\n"
        wrong_run = dict(self.valid, run_id="run_other")
        wrong_thread = dict(self.valid, thread_id="wrong-thread")
        no_profile = {k: v for k, v in self.valid.items() if k != "profile_digest"}
        no_approval = {k: v for k, v in self.valid.items() if k != "approval_authority"}
        return {
            "malformed_json": b"{ this is not json\n",
            "truncated": good.encode()[:max(1, len(good) // 2)],
            "wrong_run_id": (json.dumps(wrong_run, sort_keys=True, indent=2) + "\n").encode(),
            "wrong_thread_id": (json.dumps(wrong_thread, sort_keys=True, indent=2) + "\n").encode(),
            "missing_profile_binding": (json.dumps(no_profile, sort_keys=True, indent=2) + "\n").encode(),
            "missing_approval_binding": (json.dumps(no_approval, sort_keys=True, indent=2) + "\n").encode(),
        }

    #: The five recovery routes, as the CLI argv each one is.
    def _routes(self) -> dict[str, list[str]]:
        common = ["--run-id", self.RUN, "--artifact-base", str(self.base),
                  "--adapter", "standalone", "--json"]
        return {
            "resume": ["resume", *common],
            "cancel": ["resume", *common, "--cancel"],
            "abandon": ["resume", *common, "--abandon"],
            "recover": ["recover", *common],
            "watchdog_once": ["watchdog", "once", *common],
        }

    def _invoke(self, argv: list[str]) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        code = -1
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = launcher.run_cli(argv)
        return code, out.getvalue() + err.getvalue()

    def test_every_corruption_refuses_at_the_authority_boundary_on_every_route(self) -> None:
        auth_code = launcher.STANDALONE_ADAPTER_REQUIRES_LEDGER
        for case, payload in self._corruptions().items():
            for route, argv in self._routes().items():
                with self.subTest(case=case, route=route):
                    self.auth_path.write_bytes(payload)
                    before = self.auth_path.read_bytes()
                    code, output = self._invoke(argv)
                    self.assertNotEqual(code, 0, f"{case}/{route}: did not refuse")
                    self.assertIn(auth_code, output,
                                  f"{case}/{route}: not the typed AUTHORITY refusal "
                                  f"({auth_code}); got:\n{output[:600]}")
                    # The failure is at the authority boundary, BEFORE any recovery code:
                    # a `RECOVERY_*` code masquerading as the refusal is exactly the B1'/B2'
                    # false positive this asserts against.
                    self.assertNotIn("RECOVERY_", output,
                                     f"{case}/{route}: a downstream RECOVERY_* code fired "
                                     f"instead of the authority refusal:\n{output[:600]}")
                    self.assertEqual(self.auth_path.read_bytes(), before,
                                     f"{case}/{route}: a refusal REWROTE the authority")

    def test_a_valid_authority_is_not_refused_at_the_boundary(self) -> None:
        """The control: with the UNTOUCHED authority, no route refuses at the authority
        boundary -- so a corruption refusal above is the authority check firing, not a
        blanket rejection.  (Downstream outcomes may still vary; only the authority code
        must be ABSENT.)"""
        auth_code = launcher.STANDALONE_ADAPTER_REQUIRES_LEDGER
        for route, argv in self._routes().items():
            with self.subTest(route=route):
                self.auth_path.write_bytes(json.dumps(self.valid, indent=2).encode() + b"\n")
                _code, output = self._invoke(argv)
                self.assertNotIn(auth_code, output,
                                 f"{route}: a VALID authority was refused at the boundary")


# =====================================================================================
# B4 -- explicit audited profile migration
# =====================================================================================
class B4AuditedProfileMigrationTests(unittest.TestCase):
    """B4.  A profile-digest mismatch is resolved by an EXPLICIT, AUDITED migration -- a
    named CLI verb that writes a durable audit record and re-binds the authority
    create-once -- not by prose or a hand edit.  Red at `87f6179`: there is no
    `migrate_standalone_profile`, no migration verb and no audit log."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-b4-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt-a").mkdir()
        (self.base / "wt-b").mkdir()
        self.run = "run_b4mig"
        self.spec_a = stub_profile_spec("alive", worktree=str(self.base / "wt-a"))
        self.spec_b = stub_profile_spec("alive", worktree=str(self.base / "wt-b"))
        self.ledger = FileRuntimeStateStore(self.base / "l.json")
        launcher.publish_standalone_launch_bindings(
            self.base, self.run, profile_spec=self.spec_a,
            runtime_state_path=self.ledger.path, thread_id="t")
        self.new_profile = self.base / "new_profile.json"
        self.new_profile.write_text(json.dumps(self.spec_b))

    def _migrate(self, *extra: str) -> int:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            return launcher.run_cli([
                "migrate-standalone-profile", "--run-id", self.run, "--thread-id", "t",
                "--artifact-base", str(self.base),
                "--standalone-profile", str(self.new_profile), *extra])

    def test_a_mismatched_override_is_refused_before_migration(self) -> None:
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.standalone_recovery_composition(
                self.base, self.run, thread_id="t", ledger=self.ledger,
                pause_row_journal=launcher._standalone_pause_row_journal(self.base, self.run),
                profile_override=self.spec_b)
        self.assertIn(launcher.STANDALONE_PROFILE_DIGEST_MISMATCH, str(caught.exception))

    def test_an_unattributable_migration_is_refused(self) -> None:
        self.assertNotEqual(self._migrate("--actor-id", "", "--reason", "x"), 0,
                            "a migration with no actor must be refused")
        self.assertNotEqual(self._migrate("--actor-id", "alice", "--reason", ""), 0,
                            "a migration with no reason must be refused")
        self.assertEqual(launcher.read_standalone_migrations(self.base, self.run), (),
                         "a refused migration wrote an audit record")

    def test_migration_writes_a_durable_audit_record_and_rebinds_create_once(self) -> None:
        old = launcher.load_standalone_authority(self.base, self.run, "t")["profile_digest"]
        self.assertEqual(self._migrate("--actor-id", "alice", "--reason", "retune worktree"),
                         0, "the migration was refused")
        new = launcher.load_standalone_authority(self.base, self.run, "t")["profile_digest"]
        self.assertNotEqual(new, old, "the authority was not re-bound")
        self.assertEqual(new, launcher.profile_digest(self.spec_b))
        # F-001: EXACTLY ONE committed record (the two-phase protocol also wrote a prepared
        # record, so the raw log has both; the committed one is the truthful migration).
        committed = launcher.standalone_committed_migrations(self.base, self.run, "t")
        self.assertEqual(len(committed), 1, "expected exactly one committed migration")
        entry = committed[0]
        self.assertEqual(entry["old_profile_digest"], old)
        self.assertEqual(entry["new_profile_digest"], new)
        self.assertEqual(entry["actor"], "alice")
        self.assertEqual(entry["reason"], "retune worktree")
        self.assertTrue(entry.get("migration_id"), "the record carries no operation id")
        self.assertTrue(entry.get("migrated_at"), "the audit record carries no timestamp")
        raw = launcher.read_standalone_migrations(self.base, self.run, "t")
        self.assertTrue(any(r.get("state") == launcher.MIGRATION_PREPARED for r in raw),
                        "the two-phase protocol wrote no prepared record")
        # After migration the NEW profile is the accepted exact-digest restatement.
        adapter, _j, _p = launcher.standalone_recovery_composition(
            self.base, self.run, thread_id="t", ledger=self.ledger,
            pause_row_journal=launcher._standalone_pause_row_journal(self.base, self.run),
            profile_override=self.spec_b)
        self.assertTrue(adapter.runtime.profile.worktree.endswith("wt-b"))

    def test_a_noop_migration_to_the_bound_digest_is_refused(self) -> None:
        same = self.base / "same.json"
        same.write_text(json.dumps(self.spec_a))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = launcher.run_cli([
                "migrate-standalone-profile", "--run-id", self.run, "--thread-id", "t",
                "--artifact-base", str(self.base), "--standalone-profile", str(same),
                "--actor-id", "alice", "--reason", "no change"])
        self.assertNotEqual(code, 0, "a no-op migration must be refused")
        self.assertEqual(launcher.standalone_committed_migrations(self.base, self.run), (),
                         "a refused no-op migration committed a record")


# =====================================================================================
# F-001 -- audited migration is crash-consistent and replay-safe
# =====================================================================================
class _MigrationCrash(Exception):
    pass


class F001MigrationIsCrashConsistentAndReplaySafeTests(unittest.TestCase):
    """Final Adversarial Review F-001.  The migration is a two-phase (prepared -> committed)
    protocol keyed by a stable operation id; a crash at ANY durable-write boundary is
    reconciled deterministically from the authority digest, and replay is idempotent.  Red
    at `0678c33`: `migrate_standalone_profile` appended a completed audit record BEFORE the
    re-bind, so a crash left a ghost completion and replay duplicated it; there was no
    `migration_id`, no prepared/committed state, and no `reconcile_standalone_migrations`."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-f1mig-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        (self.base / "wt-a").mkdir()
        (self.base / "wt-b").mkdir()
        self.run = "run_f1mig"
        self.spec_a = stub_profile_spec("alive", worktree=str(self.base / "wt-a"))
        self.spec_b = stub_profile_spec("alive", worktree=str(self.base / "wt-b"))
        self.ledger = FileRuntimeStateStore(self.base / "l.json")
        launcher.publish_standalone_launch_bindings(
            self.base, self.run, profile_spec=self.spec_a,
            runtime_state_path=self.ledger.path, thread_id="t")
        self.new_digest = launcher.profile_digest(self.spec_b)

    def test_a_crash_at_every_boundary_converges_to_one_committed_migration(self) -> None:
        # Durable-write boundaries in one migration: prepared append, archive write(s),
        # authority re-bind write, committed append.  Cover them all.
        for crash_after in range(1, 6):
            with self.subTest(crash_after=crash_after):
                # Fresh run per crash point.
                run = f"run_f1c{crash_after}"
                launcher.publish_standalone_launch_bindings(
                    self.base, run, profile_spec=self.spec_a,
                    runtime_state_path=(self.base / f"l{crash_after}.json").resolve(),
                    thread_id="t")
                old = launcher.load_standalone_authority(self.base, run, "t")["profile_digest"]
                real_append, real_write = launcher._durable_append, launcher._durable_write
                n = {"i": 0}

                def wrap(fn):
                    def inner(*a, **k):
                        n["i"] += 1
                        out = fn(*a, **k)
                        if n["i"] == crash_after:
                            raise _MigrationCrash("boom")
                        return out
                    return inner
                launcher._durable_append = wrap(real_append)   # type: ignore[assignment]
                launcher._durable_write = wrap(real_write)     # type: ignore[assignment]
                try:
                    launcher.migrate_standalone_profile(
                        self.base, run, thread_id="t", new_profile_spec=self.spec_b,
                        actor="alice", reason="retune")
                except _MigrationCrash:
                    pass
                finally:
                    launcher._durable_append = real_append     # type: ignore[assignment]
                    launcher._durable_write = real_write        # type: ignore[assignment]
                # Recovery: a plain authority read reconciles the interrupted migration.
                recovered = launcher.load_standalone_authority(self.base, run, "t")["profile_digest"]
                # NO ghost committed record: at most one committed, and only if the re-bind
                # durably landed (recovered == new).
                committed = launcher.standalone_committed_migrations(self.base, run, "t")
                if recovered == self.new_digest:
                    self.assertEqual(len(committed), 1,
                                     f"crash_after={crash_after}: rebind landed but not one committed")
                else:
                    self.assertEqual(recovered, old,
                                     f"crash_after={crash_after}: authority half-applied")
                    self.assertEqual(committed, (),
                                     f"crash_after={crash_after}: GHOST committed with old digest")
                # Replay of the identical operation is idempotent: converges to exactly one
                # committed record and the new digest, no duplicate.
                launcher.migrate_standalone_profile(
                    self.base, run, thread_id="t", new_profile_spec=self.spec_b,
                    actor="alice", reason="retune")
                final = launcher.load_standalone_authority(self.base, run, "t")["profile_digest"]
                self.assertEqual(final, self.new_digest,
                                 f"crash_after={crash_after}: replay did not converge to new digest")
                self.assertEqual(
                    len(launcher.standalone_committed_migrations(self.base, run, "t")), 1,
                    f"crash_after={crash_after}: replay produced a duplicate committed record")

    def test_replay_after_a_clean_commit_is_idempotent(self) -> None:
        launcher.migrate_standalone_profile(
            self.base, self.run, thread_id="t", new_profile_spec=self.spec_b,
            actor="alice", reason="retune")
        first = launcher.standalone_committed_migrations(self.base, self.run, "t")
        self.assertEqual(len(first), 1)
        # Replaying the SAME operation appends no second committed record and returns it.
        again = launcher.migrate_standalone_profile(
            self.base, self.run, thread_id="t", new_profile_spec=self.spec_b,
            actor="alice", reason="retune")
        self.assertEqual(again["migration_id"], first[0]["migration_id"])
        self.assertEqual(
            len(launcher.standalone_committed_migrations(self.base, self.run, "t")), 1,
            "replay after a clean commit duplicated the committed record")

    # ---- iteration-5 B1: the replay key must not alias distinct epochs ------------------
    def _authority_digest(self, run: str) -> str:
        return launcher.load_standalone_authority(self.base, run, "t")["profile_digest"]

    def _migrate(self, run: str, spec, actor: str, reason: str) -> dict:
        return launcher.migrate_standalone_profile(
            self.base, run, thread_id="t", new_profile_spec=spec, actor=actor, reason=reason)

    def test_a_cross_epoch_a_b_a_history_never_aliases_a_later_migration(self) -> None:
        # B1: A->B (alice/retune), B->A (bob/revert), then a DISTINCT A->B (alice/retune).
        # Red at the iteration-5 tree: the third returned the FIRST committed record, the
        # authority stayed on A and only two records existed.  It must move to B with THREE.
        run = "run_f1epoch"
        launcher.publish_standalone_launch_bindings(
            self.base, run, profile_spec=self.spec_a,
            runtime_state_path=(self.base / "lep.json").resolve(), thread_id="t")
        digest_a = launcher.profile_digest(self.spec_a)
        digest_b = launcher.profile_digest(self.spec_b)
        self._migrate(run, self.spec_b, "alice", "retune")
        self.assertEqual(self._authority_digest(run), digest_b)
        self._migrate(run, self.spec_a, "bob", "revert")
        self.assertEqual(self._authority_digest(run), digest_a)
        third = self._migrate(run, self.spec_b, "alice", "retune")
        self.assertEqual(self._authority_digest(run), digest_b,
                         "the distinct later A->B must move the authority to B, not alias")
        committed = launcher.standalone_committed_migrations(self.base, run, "t")
        self.assertEqual(len(committed), 3,
                         "A->B, B->A, A->B must record three committed migrations")
        self.assertEqual(third["new_profile_digest"], digest_b)
        # The two A->B operations are DISTINCT operation ids (different epoch), so the later
        # one can never be mistaken for a replay of the earlier one.
        ab_ids = [c["migration_id"] for c in committed
                  if c.get("new_profile_digest") == digest_b]
        self.assertEqual(len(ab_ids), 2)
        self.assertNotEqual(ab_ids[0], ab_ids[1],
                            "same actor/reason/target at a different epoch must not alias one id")

    def test_a_crash_at_every_boundary_on_a_later_epoch_operation_converges(self) -> None:
        digest_a = launcher.profile_digest(self.spec_a)
        digest_b = launcher.profile_digest(self.spec_b)
        for crash_after in range(1, 6):
            with self.subTest(crash_after=crash_after):
                run = f"run_f1lc{crash_after}"
                launcher.publish_standalone_launch_bindings(
                    self.base, run, profile_spec=self.spec_a,
                    runtime_state_path=(self.base / f"llc{crash_after}.json").resolve(),
                    thread_id="t")
                # Two clean earlier epochs first: A->B, B->A.  Authority is back at A.
                self._migrate(run, self.spec_b, "alice", "retune")
                self._migrate(run, self.spec_a, "bob", "revert")
                self.assertEqual(self._authority_digest(run), digest_a)
                # Crash the LATER (third) A->B at durable-write boundary `crash_after`.
                real_append, real_write = launcher._durable_append, launcher._durable_write
                n = {"i": 0}

                def wrap(fn):
                    def inner(*a, **k):
                        n["i"] += 1
                        out = fn(*a, **k)
                        if n["i"] == crash_after:
                            raise _MigrationCrash("boom")
                        return out
                    return inner
                launcher._durable_append = wrap(real_append)   # type: ignore[assignment]
                launcher._durable_write = wrap(real_write)     # type: ignore[assignment]
                try:
                    self._migrate(run, self.spec_b, "alice", "retune")
                except _MigrationCrash:
                    pass
                finally:
                    launcher._durable_append = real_append     # type: ignore[assignment]
                    launcher._durable_write = real_write        # type: ignore[assignment]
                recovered = self._authority_digest(run)         # a read reconciles the attempt
                committed = launcher.standalone_committed_migrations(self.base, run, "t")
                # No ghost: the two earlier epochs are committed, and the third is committed
                # only if its re-bind durably landed (roll forward); otherwise it rolled back.
                if recovered == digest_b:
                    self.assertEqual(len(committed), 3,
                                     f"crash_after={crash_after}: rebind landed but not three committed")
                else:
                    self.assertEqual(recovered, digest_a,
                                     f"crash_after={crash_after}: authority half-applied")
                    self.assertEqual(len(committed), 2,
                                     f"crash_after={crash_after}: GHOST/duplicate committed record")
                # Replay the later operation: converges to B with exactly three committed.
                self._migrate(run, self.spec_b, "alice", "retune")
                self.assertEqual(self._authority_digest(run), digest_b,
                                 f"crash_after={crash_after}: replay did not converge to B")
                self.assertEqual(
                    len(launcher.standalone_committed_migrations(self.base, run, "t")), 3,
                    f"crash_after={crash_after}: replay did not converge to three committed")


if __name__ == "__main__":
    unittest.main()
