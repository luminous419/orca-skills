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
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
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
# L1 -- refusal detection excludes the runtime's own delivered prompt echo
# =====================================================================================
class L1RefusalExcludesEchoedPromptTests(unittest.TestCase):
    PROMPT = ("Implement the login form. Show 'login required, please sign in' when the "
              "session lapsed, and confirm deletion with a [y/N] question.")

    def test_the_echoed_prompt_does_not_fire_a_refusal(self) -> None:
        transcript = self.PROMPT + "\r\n" + '{"type":"assistant"}' + "\r\n"
        self.assertTrue(lifecycle.classify_refusals(transcript),
                        "the raw transcript should trip the pattern (the base behaviour)")
        self.assertEqual(
            lifecycle.classify_refusals(transcript, exclude_text=self.PROMPT), (),
            "the runtime's OWN delivered prompt was classified as a runtime refusal")

    def test_a_real_login_line_still_fires_after_excluding_the_prompt(self) -> None:
        transcript = self.PROMPT + "\r\nnot logged in\r\n"
        self.assertTrue(
            lifecycle.classify_refusals(transcript, exclude_text=self.PROMPT),
            "excluding the prompt must not blind the scan to a genuine refusal line")

    def test_b3_identical_blocking_line_in_prompt_and_runtime_only_the_prompt_is_excluded(self) -> None:
        """B3.  The exclusion is bound to the runtime's OWN echo SPAN, not to content: a
        prompt that quotes ``not logged in`` on its own line AND a genuine later runtime
        ``not logged in`` collide, and only the prompt-origin occurrence is excluded -- the
        runtime one still fires.  Iteration 1's content subtraction removed BOTH."""
        prompt = ("You are the WORKER.\nHandle the case where the CLI prints:\n"
                  "not logged in\nand raise ValueError.")
        # The runtime's echo of the delivered prompt comes FIRST (post-delivery region),
        # then a GENUINE runtime refusal emitting the identical line.
        transcript = (prompt + "\r\n" + '{"type":"assistant"}' + "\r\n"
                      + "not logged in" + "\r\n")
        self.assertTrue(lifecycle.classify_refusals(transcript),
                        "the raw transcript should trip the pattern")
        self.assertTrue(
            lifecycle.classify_refusals(transcript, exclude_text=prompt),
            "the GENUINE runtime `not logged in` after the echo span was hidden -- the "
            "exclusion is content subtraction, not span-bound provenance")
        # And with ONLY the echo (no genuine later line) the exclusion silences it.
        echo_only = prompt + "\r\n" + '{"type":"assistant"}' + "\r\n"
        self.assertEqual(
            lifecycle.classify_refusals(echo_only, exclude_text=prompt), (),
            "the runtime's own echoed prompt line must not fire a refusal")


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
        log = launcher.read_standalone_migrations(self.base, self.run, "t")
        self.assertEqual(len(log), 1, "no durable audit record was written")
        entry = log[0]
        self.assertEqual(entry["old_profile_digest"], old)
        self.assertEqual(entry["new_profile_digest"], new)
        self.assertEqual(entry["actor"], "alice")
        self.assertEqual(entry["reason"], "retune worktree")
        self.assertTrue(entry.get("migrated_at"), "the audit record carries no timestamp")
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
        self.assertEqual(launcher.read_standalone_migrations(self.base, self.run), (),
                         "a refused no-op migration wrote an audit record")


if __name__ == "__main__":
    unittest.main()
