"""OS-37 BUGFIX: one mutation-sensitive regression per consolidated-external-review finding.

Twelve findings, twelve locks.  Every test here FAILED at `aba1032` and passes after the
fix, and each one is written so that REVERTING ITS FIX makes it fail again -- which is the
only property that makes a regression test worth having.

**The production path is the path under test.**  Findings #2, #3, #4, #6, #7, #8 and #10 are
driven through `launcher.run_cli` / `launcher.build_standalone_adapter` ->
`StandaloneProfile` -> `StandaloneAdapter` -> the real graph -> settlement, because finding
#6 is *precisely* that the previous evidence was gathered by constructing a
`StandaloneSession` directly, below the production entry point, and therefore established
nothing about the composition an operator actually runs.  Where a test does reach for a
session it says why, and it is never to supply something the production path cannot.

The agent is the repository's own recorded-stream fixture, replayed byte-for-byte through
the native stub (`OS37_STUB_MODE=replay-stream`).  The recordings under
`scripts/fixtures/os37/streams/` are real captured output from the two installed CLIs
(D4.0 M-8 / M-14 / M-15), CRLF line endings and all, so the settlement path is exercised
against the bytes a real CLI produced rather than against an idealisation of them.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import ci_lane, os37_native_stub as native_stub  # noqa: E402
from scripts.deterministic_workflow import (contracts, launcher,  # noqa: E402
                                            pause_policy, routing,
                                            standalone_env as env_policy,
                                            standalone_journal as journal_mod)
from scripts.deterministic_workflow.runtime_state import (  # noqa: E402
    InMemoryRuntimeStateStore)
from scripts.deterministic_workflow.standalone_profile import (  # noqa: E402
    AuthProbe, CompletionSelector, CredentialContractViolation, DeliveryProofSelector,
    ReadinessSelector, ResultBodySelector, StandaloneProfile, Timeouts,
    profile_from_mapping)

REPO = Path(__file__).resolve().parent.parent
STREAMS = REPO / "scripts" / "fixtures" / "os37" / "streams"

#: Spelled in two pieces on purpose: this module's own sweep would otherwise flag the
#: constant it sweeps FOR.  It is the only literal in the file that would match.
_RUN_DIR_PREFIX = "artifacts/" + "runs/"
CONFORMANCE_RECORD = REPO / "docs" / "conformance" / "OS37_CONFORMANCE.md"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(REPO), capture_output=True, text=True,
                          check=False)


def _require_git() -> str:
    """Fail-closed, never skip: the git-availability skips are deliberately untolerated."""
    if shutil.which("git") is None:                       # pragma: no cover - CI has git
        raise AssertionError("git is not on PATH; this contract cannot be checked and a "
                             "skip here is exactly the silent gap the manifest forbids")
    return ""


# =====================================================================================
# F1 -- the clean checkout
# =====================================================================================
class F01CleanCheckoutTests(unittest.TestCase):
    """All six CI jobs were red on a clean checkout.  Three independent causes."""

    def test_the_conformance_record_the_suite_reads_is_tracked_in_git(self) -> None:
        """(a) `test_os37_conformance_record` depended on an UNTRACKED run artifact.

        Its 13 tests read `artifacts/runs/run_54d90086bd75/CONFORMANCE.md`, which is a run
        artifact and is deliberately not committed, so every one of them failed in `setUp`
        on a clean checkout while passing on the developer host where the directory
        happened to exist.

        Mutation-sensitivity: point `RECORD` back at any `artifacts/runs/**` path and this
        fails, because `git ls-files` reports nothing for it.
        """
        _require_git()
        from scripts import test_os37_conformance_record as module
        record = Path(module.RECORD).resolve()
        self.assertTrue(record.is_file(), f"{record} does not exist")
        relative = record.relative_to(REPO)
        tracked = _git("ls-files", "--error-unmatch", str(relative))
        self.assertEqual(
            tracked.returncode, 0,
            f"{relative} is not tracked in git, so a clean checkout cannot read it; "
            "AC-37-21's record is a deliverable and must be a tracked fixture")
        self.assertNotIn(
            "artifacts/runs", str(relative),
            "the record must not live under a run-artifact directory again")

    def test_no_os37_suite_is_grounded_in_a_local_run_directory(self) -> None:
        """The general form of (a), scoped to the suites this ticket owns.

        A repository-wide rule would be the WRONG rule: 558 artifact files from prior runs
        ARE committed and several older suites legitimately read them.  What must hold is
        that OS-37's own tests -- the ones that were red on a clean checkout -- name no
        local run directory at all, neither as a literal nor as a `REPO / "artifacts"`
        expression.

        Mutation-sensitivity: point any OS-37 module back at
        `artifacts/runs/run_54d90086bd75/...` and it appears here.
        """
        import ast
        offenders: list[str] = []
        for path in sorted((REPO / "scripts").glob("test_os37_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value.strip().startswith(_RUN_DIR_PREFIX + "run_"):
                        offenders.append(f"{path.name}: literal {node.value[:60]!r}")
                if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                        and isinstance(node.right, ast.Constant)
                        and node.right.value == "artifacts"):
                    offenders.append(f"{path.name}: builds a path into artifacts/")
        self.assertEqual(
            offenders, [],
            "an OS-37 suite is grounded in a local run directory; a clean checkout has "
            "none:\n" + "\n".join(offenders))

    def test_the_recorded_streams_keep_their_bytes_and_are_exempt_from_the_gate(self) -> None:
        """(b) trailing whitespace in the captured stream fixtures.

        The bytes are a MEASUREMENT: a pty in canonical mode translates NL to CR NL, so a
        real recording ends every line `\\r\\n` and git reads that CR as trailing
        whitespace.  The two admissible fixes were "change the whitespace check" and
        "change the fixture's storage form"; rewriting the recording was not one of them,
        because `standalone_capture.structured_lines` and the M-14 / M-15 / M-8 selector
        tests read these files as the literal output the CLI produced.

        The exemption was chosen.  BOTH halves are asserted, and neither can be satisfied
        by the other's fix: the CR bytes are still there, AND the gate passes.
        """
        _require_git()
        recordings = sorted(STREAMS.glob("*.stream"))
        self.assertTrue(recordings, "the recorded stream fixtures are gone")
        for path in recordings:
            with self.subTest(fixture=path.name):
                raw = path.read_bytes()
                self.assertIn(b"\r\n", raw,
                              "the recording no longer carries the pty's CR LF endings; "
                              "the fixture was rewritten instead of exempted")
                attrs = _git("check-attr", "whitespace", "text", "--",
                             str(path.relative_to(REPO)))
                self.assertEqual(attrs.returncode, 0)
                self.assertIn("whitespace: unset", attrs.stdout,
                              "the recording is not exempt from git's whitespace gate")
                self.assertIn("text: unset", attrs.stdout,
                              "the recording is not pinned against eol conversion")
        checked = _git("diff", "--check", "HEAD", "--",
                       str(STREAMS.relative_to(REPO)))
        self.assertEqual(checked.stdout, "",
                         f"git's whitespace gate still reports the recordings:\n"
                         f"{checked.stdout}")

    def test_the_negative_pty_assertion_is_platform_declared_not_platform_lucky(self) -> None:
        """(c) the Linux-sensitive negative PTY assertion.

        `test_without_draining_the_exiting_child_is_not_reapable` asserted that a
        `SIGKILL`ed pty session leader stays UNREAPABLE until the master is drained.  That
        is a MEASURED darwin behaviour and the assertion carried no platform condition, so
        on a Linux CI runner it failed for a reason that says nothing about this
        repository -- while on the developer's macOS host it passed and looked like a
        contract.

        This asserts the SHAPE of the fix rather than re-running it: the wedge claim is
        made only for platforms where it was measured, the unmeasured platforms are not
        given a claim nobody established, and the PORTABLE half (the drain recovers real
        bytes) is asserted unconditionally so the test is not vacuous anywhere.
        """
        import ast
        import inspect

        from scripts import test_os37_pty_supervisor as pty_tests
        case = pty_tests.DrainIsATeardownObligationTests
        self.assertEqual(
            case.WEDGE_MEASURED_PLATFORMS, ("darwin",),
            "the measured-platform declaration changed; a platform may only be added here "
            "with a measurement, never to make a runner green.  Running the suite on a "
            "platform is NOT such a measurement: the wedge equality sits behind a "
            "`sys.platform` BRANCH, not behind a skip, so a green job on a platform absent "
            "from this tuple evaluated only the portable half")
        # ---- OS-37 correction R2: what the real ubuntu-latest run DID retire -----------
        # The two declarations are separate on purpose and are asserted separately.  The
        # suite has now really executed on Linux -- GitHub Actions run 34541433633 on
        # commit 5f9f4c4, six green matrix jobs, `test_os37_pty_supervisor` in neither skip
        # manifest -- so "Linux pty behaviour is reasoned from code, not measured" is no
        # longer true and must not be re-asserted.  What that run did NOT do is evaluate
        # the wedge equality, which is why the tuple above is unchanged.
        self.assertEqual(
            case.PTY_SUITE_EXERCISED_PLATFORMS, ("darwin", "linux"),
            "the exercised-platform declaration changed; `linux` was established by a real "
            "ubuntu-latest CI run and removing it would re-assert a limitation that has "
            "been retired, while adding a platform needs a run of its own")
        source = inspect.getsource(case.test_without_draining_the_exiting_child_is_not_reapable)
        tree = ast.parse(source.lstrip() if source.startswith("    ") else source) \
            if not source.startswith("    ") else ast.parse(
                "\n".join(line[4:] if line.startswith("    ") else line
                          for line in source.splitlines()))
        guarded = [node for node in ast.walk(tree)
                   if isinstance(node, ast.If)
                   and "WEDGE_MEASURED_PLATFORMS" in ast.dump(node.test)]
        self.assertTrue(
            guarded,
            "the wedge assertion is unconditioned again; it is a darwin measurement and "
            "an unconditioned form makes every non-darwin runner fail for the wrong reason")
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "assertEqual"):
                self.assertTrue(
                    any(isinstance(parent, ast.If) for parent in ast.walk(tree)
                        if node in ast.walk(parent)),
                    "the reapability equality escaped its platform guard")
        self.assertIn(
            "assertGreater", source,
            "the PORTABLE half is gone; without it this test asserts nothing at all on a "
            "platform where the wedge was never measured")

    def test_the_wedge_set_can_never_exceed_the_exercised_set(self) -> None:
        """The structural form of the over-claim, refused rather than discouraged.

        A platform may only be named MEASURED for the wedge if this suite has at least RUN
        there -- so the strictly stronger claim can never be made without the weaker one.
        Its own case, deliberately: the two tuple equalities above would fire first and mask
        it, and an invariant that only ever fails behind another assertion is not locked.

        Mutation-sensitivity: name a platform in `WEDGE_MEASURED_PLATFORMS` that is absent
        from `PTY_SUITE_EXERCISED_PLATFORMS` and this reports exactly which one.
        """
        from scripts import test_os37_pty_supervisor as pty_tests
        case = pty_tests.DrainIsATeardownObligationTests
        overclaimed = sorted(set(case.WEDGE_MEASURED_PLATFORMS)
                             - set(case.PTY_SUITE_EXERCISED_PLATFORMS))
        self.assertEqual(
            overclaimed, [],
            "the wedge is claimed MEASURED on a platform this suite has never even run "
            f"on: {overclaimed}")


# =====================================================================================
# the production composition, used by F2 / F3 / F4 / F6 / F7 / F8 / F10
# =====================================================================================
def _stub_dir() -> Path:
    built = native_stub.native_stub_dir()
    if built is None:                                     # pragma: no cover - CI has cc
        raise AssertionError(native_stub.NO_COMPILER_REASON)
    return built


def replay_profile(*, stream: Path, exit_code: int, worktree: str,
                   rehearsal_stream: Path | None = None,
                   **overrides) -> StandaloneProfile:
    """The profile an operator would commit for a CLI that speaks the recorded stream.

    `identity_binding="adopted"` because the recordings carry the session id the REAL CLI
    minted during the capture; adopting it is the same binding mode the shipping Codex
    profile uses and it is what lets a byte-exact recording drive R-B honestly.  Nothing
    else about the profile is special: the selectors are the shipping Claude profile's.
    """
    fields = dict(
        driver="claude", binary="os37-stub-cli", supported_range=((1, 0, 0), (2, 0, 0)),
        bin_dirs=(str(_stub_dir()),),
        worktree=worktree,
        delivery_mode="launch_with_prompt", identity_binding="adopted",
        # `OS37_STUB_AUTH` answers the profile-declared credential probe; the recorded
        # stream is the AGENT TURN.  A real CLI answers `auth status` whatever else it is
        # doing, and conflating the two would make every replay refuse at preflight.
        driver_env={"OS37_STUB_MODE": "replay-stream",
                    "OS37_STUB_STREAM": str(stream),
                    "OS37_STUB_AUTH": "ok",
                    "OS37_STUB_REHEARSAL_STREAM": str(rehearsal_stream or stream),
                    "OS37_STUB_EXIT_CODE": str(exit_code)},
        readiness_records=(ReadinessSelector(channel="structured", record_type="system",
                                            session_field="session_id"),),
        delivery_proofs=(DeliveryProofSelector(channel="structured",
                                               record_type="assistant"),),
        completion_records=(CompletionSelector(channel="structured", record_type="result",
                                               error_field="is_error",
                                               success_field="terminal_reason",
                                               success_values=("completed",)),),
        result_body_records=(ResultBodySelector(channel="structured", record_type="result",
                                                body_field="result"),),
        auth_probe=AuthProbe(args=("auth", "status")),
        auth_markers=(("error", "authentication_failed"),),
        timeouts=Timeouts(preflight_timeout_ms=5_000, readiness_timeout_ms=20_000,
                          delivery_verify_timeout_ms=20_000,
                          completion_timeout_ms=30_000),
    )
    fields.update(overrides)
    return StandaloneProfile(**fields)


def profile_spec(profile: StandaloneProfile) -> dict:
    """The JSON an operator writes.  Round-tripped, so the CLI door is what is tested."""
    return {
        "driver": profile.driver, "binary": profile.binary,
        "supported_range": [list(profile.supported_range[0]),
                            list(profile.supported_range[1])],
        "bin_dirs": list(profile.bin_dirs), "worktree": profile.worktree,
        "delivery_mode": profile.delivery_mode,
        "identity_binding": profile.identity_binding,
        "driver_env": dict(profile.driver_env),
        "auth_secret_ref": dict(profile.auth_secret_ref),
        "readiness_records": [{"channel": s.channel, "record_type": s.record_type,
                               "session_field": s.session_field}
                              for s in profile.readiness_records],
        "delivery_proofs": [{"channel": s.channel, "record_type": s.record_type,
                             "item_type": s.item_type}
                            for s in profile.delivery_proofs],
        "completion_records": [{"channel": s.channel, "record_type": s.record_type,
                                "error_field": s.error_field,
                                "success_field": s.success_field,
                                "success_values": list(s.success_values)}
                               for s in profile.completion_records],
        "result_body_records": [{"channel": s.channel, "record_type": s.record_type,
                                 "body_field": s.body_field, "item_type": s.item_type}
                                for s in profile.result_body_records],
        "auth_probe": {"args": list(profile.auth_probe.args)} if profile.auth_probe else None,
        "auth_markers": [list(m) for m in profile.auth_markers],
        "timeouts": {"preflight_timeout_ms": profile.timeouts.preflight_timeout_ms,
                     "readiness_timeout_ms": profile.timeouts.readiness_timeout_ms,
                     "delivery_verify_timeout_ms":
                         profile.timeouts.delivery_verify_timeout_ms,
                     "completion_timeout_ms": profile.timeouts.completion_timeout_ms},
    }


WORKER_INTENT_KEYS = {
    "schema_version": "os40.action.v2", "command_id": "cmd-1",
    "action_kind": "DISPATCH_AGENT", "phase": "IMPLEMENTATION", "phase_iteration": 1,
    "final_review_iteration": 0, "round_kind": "PHASE_GATE", "artifact_binding": {},
    "repository_binding": {}, "payload_digest": "d", "repair_attempt": 0,
    "gate_iteration": 1, "artifact_contract_path": "x.md", "repair_instruction": None,
}


class _ProductionPath(unittest.TestCase):
    """Compose the adapter the way `run_cli` does, and dispatch through `adapter.start`."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        self.adapters: list = []
        self.addCleanup(self._reap_every_session)
        self.addCleanup(shutil.rmtree, self.base, True)
        self.worktree = str(self.base / "worktree")
        os.makedirs(self.worktree, exist_ok=True)

    def _reap_every_session(self) -> None:
        """Drain and reap every child this case spawned.  Never leave one behind.

        `spawn_only` returns WITHOUT pumping, by contract -- it exists so a caller can
        separate "spawned" from "settled" -- so a child that writes more than the pty
        buffer holds blocks on the master until somebody reads it.  A test that leaves one
        of those behind leaks a real process for the life of the machine, which is why this
        is a cleanup rather than a `finally` in one case.
        """
        import signal
        for adapter in self.adapters:
            runtime = getattr(adapter, "runtime", None)
            for session in list(getattr(runtime, "sessions", {}).values()):
                try:
                    session.pump(timeout_ms=50)
                except Exception:                         # noqa: BLE001 - cleanup
                    pass
                record = session.record
                if record:
                    for sig in (signal.SIGTERM, signal.SIGKILL):
                        try:
                            os.killpg(int(record["pgid"]), sig)
                        except OSError:
                            break
                    try:
                        os.waitpid(int(record["pid"]), os.WNOHANG)
                    except OSError:
                        pass
                try:
                    session.release()
                except Exception:                         # noqa: BLE001 - cleanup
                    pass

    def compose(self, profile: StandaloneProfile, *, run_id: str,
                ledger=None):
        """`build_standalone_adapter` -- the SAME call `run_cli` makes, with a real ledger."""
        ledger = ledger if ledger is not None else InMemoryRuntimeStateStore()
        adapter, state = launcher.build_standalone_adapter(
            {"run_id": run_id, "thread_id": "t", "phases": ["IMPLEMENTATION"]},
            artifact_base=self.base, run_id=run_id, runtime_state=ledger,
            profile_spec=profile_spec(profile))
        self.adapters.append(adapter)
        return adapter, state, ledger

    def intent(self, intent_id: str, *, role: str = "WORKER", run_id: str = "run_x") -> dict:
        return {**WORKER_INTENT_KEYS, "intent_id": intent_id, "run_id": run_id,
                "role": role}

    def dispatch(self, adapter, ledger, intent: dict):
        """`adapter.start`, and an ESCAPE is reported as a FAILURE rather than an error.

        Finding #8's whole subject is a dispatch outcome that left the graph as a traceback
        instead of as a verdict.  A test that lets that traceback out is reported by
        unittest as an ERROR with the runtime's own message -- which says nothing about
        what was expected -- so the escape is caught here and stated as the assertion it
        actually is.  Nothing else is caught: this re-raises as `AssertionError`, it does
        not swallow.
        """
        claim = ledger.claim(intent)
        try:
            receipt = adapter.start(intent, lease_token=claim["lease_token"])
        except BaseException as exc:                # noqa: BLE001 - the ESCAPE is the point
            raise AssertionError(
                f"the dispatch escaped `adapter.start` as {type(exc).__name__}: {exc}; a "
                "dispatch that cannot produce a verdict must still SETTLE one, so the "
                "workflow's own policy decides what happens next") from None
        return receipt, adapter.settlement(intent["intent_id"])


# =====================================================================================
# F2 / F3 -- the settlement verdict and the real stream
# =====================================================================================
class F02AuthFailureIsNeverSuccessTests(_ProductionPath):
    """A measured authentication failure was persisted as `outcome=succeeded`."""

    def test_the_committed_auth_failure_stream_with_exit_1_settles_failed(self) -> None:
        """The reproduction, through the production composition root.

        `m15_claude_auth_failure.stream` is the REAL capture: `type='result'`
        `subtype='success'` `is_error=True` `terminal_reason='api_error'`, with the measured
        `rc=1`.  `await_completion` accepted "a completion record exists AND an exit was
        proven" and `_settle` then wrote `state=COMPLETED`, `outcome=succeeded`
        unconditionally -- so this exact stream produced a SUCCEEDED settlement.

        Mutation-sensitivity: delete either the predicate call in `await_completion` or the
        `outcome` argument in `_settle` and this fails.
        """
        # The rehearsal replays the SUCCESS recording and the dispatched turn replays the
        # authentication failure: a credential that is present at preflight and rejected by
        # the API, which is exactly what D4.0 M-2 measured.  Replaying the failure at
        # preflight too would refuse before the spawn -- correct, but it would test the
        # preflight gate rather than the SETTLEMENT predicate this finding is about.
        profile = replay_profile(stream=STREAMS / "m15_claude_auth_failure.stream",
                                 rehearsal_stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=1, worktree=self.worktree)
        adapter, _state, ledger = self.compose(profile, run_id="run_authfail")
        intent = self.intent("intent-authfail", run_id="run_authfail")
        receipt, event = self.dispatch(adapter, ledger, intent)

        self.assertTrue(receipt["settled"], "an auth failure produced no settlement at all")
        self.assertEqual(receipt["outcome"], "failed",
                         "an authentication failure with rc=1 was recorded as a success")
        self.assertIsNotNone(event)
        # `.get`, deliberately: under the inverse of this fix the settlement is a SUCCESS
        # and carries no `status` at all, and a `KeyError` is not an assertion -- it says
        # the test exploded, not that it noticed.
        self.assertEqual(event["result"].get("status"), "BLOCKED",
                         f"the Worker settlement claims COMPLETE for a login failure: "
                         f"{event['result']!r}")
        self.assertEqual((event["result"].get("standalone_failure") or {}).get("reason"),
                         "error_field_set",
                         "the failure is not named; an operator cannot see which leg "
                         f"refused: {event['result']!r}")
        contracts.validate_event(intent, dict(event))     # the engine must still accept it

        rows = adapter.settlement_journal.rows_for("intent-authfail")
        settled = [row for row in rows if row["kind"] == "SETTLEMENT_OBSERVED"]
        self.assertEqual([row["outcome"] for row in settled], ["failed"],
                         "the durable journal records this dispatch as succeeded")
        self.assertEqual([row["state"] for row in settled], ["FAILED"])

    def test_every_refusing_leg_is_reachable_and_none_of_them_passes(self) -> None:
        """The predicate is CONJUNCTIVE, asserted leg by leg rather than in one case."""
        from scripts.deterministic_workflow import standalone_drivers as drivers
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        driver = drivers.driver_for(profile)
        good = {"type": "result", "is_error": False, "terminal_reason": "completed"}
        self.assertEqual(driver.completion_verdict(good, exit_status=0)["outcome"],
                         "succeeded")
        for record, exit_status, reason in (
            (None, 0, "no_completion_record"),
            ({"type": "banner"}, 0, "completion_record_undeclared"),
            ({"type": "result", "terminal_reason": "completed"}, 0, "error_field_absent"),
            ({**good, "is_error": True}, 0, "error_field_set"),
            ({**good, "terminal_reason": "api_error"}, 0, "terminal_reason_not_success"),
            (good, 1, "exit_code_nonzero"),
        ):
            with self.subTest(reason=reason):
                verdict = driver.completion_verdict(record, exit_status=exit_status)
                self.assertEqual(verdict["outcome"], "failed")
                self.assertEqual(verdict["reason"], reason)
        self.assertEqual(driver.completion_verdict(good, exit_status=None)["outcome"],
                         "unknown", "an unreported exit must never be a success")


class F03RealStreamIsParsedTests(_ProductionPath):
    """The whole JSON event stream was handed to the Markdown/single-result parser."""

    BODY = ("# Worker Result\n\n"
            "STATUS: COMPLETE\n"
            "DECISION_GATE_STATE: CLEAR\n"
            "UNIT_TEST_STATUS: PASS\n")

    def _stream_carrying(self, body: str) -> Path:
        """The REAL captured stream, with only the result record's body field replaced.

        Built at test time rather than committed, so its provenance stays honest: the
        envelope, the record order, the field set and the CRLF endings are the measured
        ones, and exactly one string differs.
        """
        source = (STREAMS / "m14_claude_genuine_turn.stream").read_text(
            encoding="utf-8", errors="replace")
        out: list[str] = []
        for line in source.split("\n"):
            stripped = line.rstrip("\r")
            try:
                record = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                out.append(line)
                continue
            if isinstance(record, dict) and record.get("type") == "result":
                record["result"] = body
                out.append(json.dumps(record) + "\r")
                continue
            out.append(line)
        target = self.base / "carrier.stream"
        target.write_text("\n".join(out), encoding="utf-8")
        return target

    def test_the_body_inside_the_real_stream_reaches_the_shared_parser(self) -> None:
        """The same body parses inside the stream that carries it.

        Before the fix the shared parser was given the WHOLE line-delimited JSON stream:
        it saw no `STATUS:` field line and no fenced record, produced `{}` plus an empty
        gate envelope, and `contracts.validate_event` rejected the settlement as
        `UNKNOWN_EVENT`.  The driver now extracts the final assistant/result BODY -- from a
        profile-declared field, so no CLI name is hard-coded -- and hands ONLY that to the
        SAME parser every other adapter uses.

        Mutation-sensitivity: drop `result_body_records` from the profile, or revert
        `_settle` to `self.capture.transcript()`, and the settlement loses `status`.
        """
        profile = replay_profile(stream=self._stream_carrying(self.BODY), exit_code=0,
                                 worktree=self.worktree)
        adapter, _state, ledger = self.compose(profile, run_id="run_body")
        intent = self.intent("intent-body", run_id="run_body")
        receipt, event = self.dispatch(adapter, ledger, intent)

        self.assertEqual(receipt["outcome"], "succeeded", receipt)
        self.assertEqual(event["result"].get("status"), "COMPLETE",
                         f"STATUS was lost inside the stream that carries it: "
                         f"{event['result']!r}")
        self.assertEqual((event["result"].get("gate") or {}).get("declared_state"),
                         "CLEAR", "the decision-gate declaration was lost")
        contracts.validate_event(intent, dict(event))

        rows = adapter.settlement_journal.rows_for("intent-body")
        settled = [row for row in rows if row["kind"] == "SETTLEMENT_OBSERVED"][-1]
        self.assertEqual(settled["source_vocabulary"]["result_body_source"],
                         "result.result",
                         "the settlement did not record which body it parsed")

    def test_the_shared_parser_is_the_one_the_other_adapters_use(self) -> None:
        """No standalone-only verdict policy, asserted rather than promised.

        The extracted body must produce the SAME parse the Orca/fake path produces for the
        same bytes.  If the standalone path ever grew its own parser this diverges.
        """
        from scripts import decision_contract
        from scripts.deterministic_workflow import standalone_drivers as drivers

        class _Body:
            body = self.BODY

        profile = replay_profile(stream=self._stream_carrying(self.BODY), exit_code=0,
                                 worktree=self.worktree)
        driver = drivers.driver_for(profile)
        text = Path(profile.driver_env["OS37_STUB_STREAM"]).read_text(encoding="utf-8")
        extracted = driver.result_body(text)
        self.assertEqual(extracted["body"], self.BODY)
        intent = self.intent("intent-parity")
        direct = decision_contract.parse_agent_settlement(_Body(), intent)

        class _Extracted:
            body = extracted["body"]

        self.assertEqual(decision_contract.parse_agent_settlement(_Extracted(), intent),
                         direct,
                         "the extracted body parses differently from the body itself")

    def test_a_profile_declaring_no_extraction_keeps_the_whole_transcript(self) -> None:
        """The fallback is explicit, and it SAYS which one it took."""
        from scripts.deterministic_workflow import standalone_drivers as drivers
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree,
                                 result_body_records=())
        extracted = drivers.driver_for(profile).result_body("anything at all")
        self.assertIsNone(extracted["body"])
        self.assertEqual(extracted["source"], "whole_transcript")


# =====================================================================================
# F4 -- three questions, three deadlines
# =====================================================================================
class F04CompletionHasItsOwnDeadlineTests(_ProductionPath):
    """A healthy multi-minute agent was declared LOST by the READINESS deadline."""

    def test_a_turn_longer_than_the_readiness_window_still_completes(self) -> None:
        """The case to protect: a real agent turn runs many minutes.

        The readiness bound is set to a value the agent's own turn EXCEEDS.  Before the
        fix `await_completion` waited under `readiness_timeout_ms`, so this dispatch was
        `LOST/exit_status_absent` with the work already done; now readiness bounds
        readiness and completion bounds completion.

        Mutation-sensitivity: point `await_completion`'s deadline back at
        `readiness_timeout_ms` and this fails with the finding's own symptom.
        """
        stream = STREAMS / "m14_claude_genuine_turn.stream"
        profile = replay_profile(
            stream=stream, exit_code=0, worktree=self.worktree,
            driver_env={"OS37_STUB_MODE": "replay-stream",
                        "OS37_STUB_STREAM": str(stream),
                        "OS37_STUB_EXIT_CODE": "0",
                        # The agent keeps working AFTER its readiness record, for longer
                        # than the readiness window allows a process to take to become
                        # ready.  That is the whole shape of the defect.
                        "OS37_STUB_WORK_MS": "2500"},
            timeouts=Timeouts(preflight_timeout_ms=5_000,
                              readiness_timeout_ms=1_000,
                              delivery_verify_timeout_ms=20_000,
                              completion_timeout_ms=30_000))
        adapter, _state, ledger = self.compose(profile, run_id="run_slow")
        intent = self.intent("intent-slow", run_id="run_slow")
        receipt, event = self.dispatch(adapter, ledger, intent)
        self.assertEqual(receipt["outcome"], "succeeded",
                         "a healthy long turn was not allowed to finish")
        self.assertIsNotNone(event)

    def test_the_three_bounds_are_three_fields_and_three_call_sites(self) -> None:
        """Separation asserted structurally, so it cannot regress by re-sharing one field."""
        import inspect

        from scripts.deterministic_workflow import standalone_runtime as runtime
        defaults = Timeouts()
        self.assertGreater(defaults.completion_timeout_ms, defaults.readiness_timeout_ms,
                           "the completion bound is no larger than the readiness bound; a "
                           "real agent turn routinely outruns a readiness probe")
        def deadline_field(function) -> str:
            """The timeout field the function's DEADLINE expression reads.

            Read from the code rather than from the source text, so the prose explaining
            the old behaviour cannot satisfy or break this.
            """
            import ast
            import textwrap
            tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute)
                        and node.attr.endswith("_timeout_ms")
                        and isinstance(node.value, ast.Attribute)
                        and node.value.attr == "timeouts"):
                    return node.attr
            return ""

        self.assertEqual(deadline_field(runtime.StandaloneSession.await_ready),
                         "readiness_timeout_ms")
        self.assertEqual(deadline_field(runtime.StandaloneSession.await_delivery),
                         "delivery_verify_timeout_ms",
                         "delivery is bounded by another question's deadline")
        self.assertEqual(deadline_field(runtime.StandaloneSession.await_completion),
                         "completion_timeout_ms",
                         "completion is bounded by the readiness deadline again")


# =====================================================================================
# F5 -- the ownership vocabularies
# =====================================================================================
class F05OwnershipVocabularyTests(_ProductionPath):
    """`contracts` and `pause_policy` disagreed, and every standalone row BLOCKED."""

    def test_the_two_vocabularies_are_equal_member_for_member(self) -> None:
        """The parity test `contracts.py`'s own comment claimed, which did not exist.

        Mutation-sensitivity: re-add `unknown` to `SETTLEMENT_AXIS` or `unverifiable` to
        `PROCESS_LIVENESS_AXIS` and this fails immediately.
        """
        for name, ours, theirs in (
            ("settlement", contracts.SETTLEMENT_AXIS, pause_policy.SETTLEMENT_OUTCOMES),
            ("worker_resource", contracts.WORKER_RESOURCE_AXIS,
             pause_policy.WORKER_RESOURCE_OUTCOMES),
            ("process_liveness", contracts.PROCESS_LIVENESS_AXIS,
             pause_policy.PROCESS_LIVENESS_STATES),
            ("cleanup_authority", contracts.CLEANUP_AUTHORITY_AXIS,
             pause_policy.CLEANUP_AUTHORITY_STATES),
        ):
            with self.subTest(axis=name):
                self.assertEqual(
                    tuple(ours), tuple(theirs),
                    f"the {name} axis differs between the runtime-neutral core and the "
                    "pause/settlement authority; a member the authority does not accept "
                    "makes every row carrying it BLOCK")

    def test_a_default_standalone_row_is_accepted_and_discharged_by_the_pause_policy(self) -> None:
        """The DEFAULT row: nothing observed at all.

        `axes_for`'s default used to be `unknown`/`unverifiable`.  `unknown != not_settled`
        skipped `executor._settlement_row`'s recovery branch, and `unverifiable` then failed
        `validate_settlement_row` outright -- so a row nobody had observed reached
        `TERMINAL_OWNERSHIP_UNKNOWN` twice over.
        """
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        adapter, _state, _ledger = self.compose(profile, run_id="run_axes")
        row = dict(adapter.account_dispatch("intent-nothing"))
        self.assertEqual(row["settlement"], "not_settled")
        self.assertEqual(row["process_liveness"], "disputed")
        for axis in ("settlement", "worker_resource", "process_liveness",
                     "cleanup_authority"):
            with self.subTest(axis=axis):
                self.assertIn(row[axis],
                              contracts.OWNERSHIP_AXIS_VOCABULARIES[axis])
                self.assertIn(row[axis], {
                    "settlement": pause_policy.SETTLEMENT_OUTCOMES,
                    "worker_resource": pause_policy.WORKER_RESOURCE_OUTCOMES,
                    "process_liveness": pause_policy.PROCESS_LIVENESS_STATES,
                    "cleanup_authority": pause_policy.CLEANUP_AUTHORITY_STATES,
                }[axis])

    def test_a_settled_and_a_recovered_standalone_row_both_pause_without_blocking(self) -> None:
        """The two rows the finding names, through `executor._settlement_row` itself.

        This is the engine's own accounting function, not a re-implementation of it: it
        calls `recover_handle`, `account_dispatch`, `recover_dispatch` and
        `require_pause_disposition` in the production order.  Before the fix BOTH rows
        raised `PauseRefused(TERMINAL_OWNERSHIP_UNKNOWN)`.
        """
        from scripts.deterministic_workflow import executor
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        adapter, _state, ledger = self.compose(profile, run_id="run_pause")
        settled_intent = self.intent("intent-settled", run_id="run_pause")
        self.dispatch(adapter, ledger, settled_intent)

        # A SECOND dispatch that is spawned but never settles: the `recovered` row.  A REAL
        # spawn (`spawn_only`, the adapter's own non-blocking verb) rather than a
        # fabricated journal row naming a pid nobody holds: since the consolidated
        # follow-up review's finding 8, `recover_handle` verifies the journal's candidate
        # against the live process table and the run's own exit sentinel, and a row for a
        # process that never existed is -- correctly -- an orphan it refuses.
        open_intent = self.intent("intent-open", run_id="run_pause")
        session = adapter.runtime.session_for(open_intent)
        adapter._journal_planned(open_intent, session)
        open_claim = ledger.claim(open_intent)
        spawned = adapter.spawn_only(open_intent, lease_token=open_claim["lease_token"],
                                     payload="work")
        self.assertEqual(spawned["start_outcome"], "ready", spawned)

        # The REFUSAL is what the finding is: before the fix both rows raised
        # `PauseRefused(TERMINAL_OWNERSHIP_UNKNOWN)`.  It is caught and stated as an
        # assertion, because an exception escaping a test is reported as an ERROR carrying
        # the runtime's own message, which says nothing about what this case expected.
        rows: dict[str, dict] = {}
        refused: list[str] = []
        for intent_id in ("intent-settled", "intent-open"):
            try:
                rows[intent_id] = executor._settlement_row(
                    adapter, adapter.pause_row_journal, intent_id,
                    now="1970-01-01T00:00:00Z")
            except Exception as exc:                # noqa: BLE001 - the REFUSAL is the point
                refused.append(f"{intent_id}: `executor._settlement_row` refused the row "
                               f"outright -- {type(exc).__name__}: {exc}")
        self.assertEqual(refused, [], "\n".join(refused))
        for intent_id, row in rows.items():
            with self.subTest(intent=intent_id):
                self.assertIn(row["terminal_disposition"],
                              pause_policy.AC1_DISCHARGING_DISPOSITIONS,
                              f"{intent_id} is {row['terminal_disposition']!r}, which does "
                              "not discharge AC-1, so the pause is refused")
        self.assertEqual(rows["intent-settled"]["settlement"], "settled")
        self.assertEqual(rows["intent-open"]["settlement"], "recovered",
                         "an unsettled dispatch was not normalized through recovery")
        self.assertEqual(rows["intent-open"]["terminal_disposition"],
                         "retained_by_named_owner",
                         "a live standalone pty session has a named owner in the journal "
                         "and must not be reported as an unowned residual")


# =====================================================================================
# F6 -- the auth probe, as a typed contract on the profile
# =====================================================================================
class F06AuthProbeIsWiredThroughTheProductionPathTests(_ProductionPath):
    """`StandaloneAdapter.start` could not supply `auth_probe_argv`, and no profile could."""

    def test_the_profile_declares_the_probe_and_the_session_uses_it(self) -> None:
        """launcher -> profile -> adapter -> session, with NOTHING injected underneath.

        The probe is observed by replacing the PROBER -- the process boundary -- not by
        passing `auth_probe_argv` in, which is the injection the finding is about.  The
        argv it receives must be the one the committed profile declares.
        """
        from scripts.deterministic_workflow import standalone_preflight as preflight

        seen: list[list[str]] = []

        def prober(argv, env, *, timeout_ms, cwd=None):
            seen.append(list(argv))
            return {"outcome": "exited", "exit_code": 0, "output": ""}

        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        adapter, _state, ledger = self.compose(profile, run_id="run_probe")
        intent = self.intent("intent-probe", run_id="run_probe")
        session = adapter.runtime.session_for(intent)
        # The probe is read off the PROFILE the launcher built; the only thing supplied
        # here is the process boundary, exactly as `harness_factory` is elsewhere.
        original = preflight.probe_on_pty
        preflight.probe_on_pty = prober
        try:
            session.start(lease_token=ledger.claim(intent)["lease_token"],
                          payload="hello")
        finally:
            preflight.probe_on_pty = original
        self.assertIn(["os37-stub-cli", "auth", "status"], seen,
                      "the profile's declared auth probe never reached preflight through "
                      "the production path")

    def test_the_probe_is_a_typed_contract_not_a_free_argv(self) -> None:
        """A profile cannot point the credential check at some other executable."""
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        self.assertEqual(profile.auth_probe_argv(),
                         ("os37-stub-cli", "auth", "status"),
                         "the probe argv is not composed from THIS profile's binary")
        with self.assertRaises(Exception):
            AuthProbe(args=())
        spec = profile_spec(profile)
        spec["auth_probe"] = ["auth", "status"]
        with self.assertRaises(Exception):
            profile_from_mapping(spec)

    # ==================================================================================
    # The corrected inventory -- SCOPE FIRST, because iteration 2's version described a
    # scope it did not have.
    #
    # Iteration 2 scanned `scripts/`, SKIPPED all of `scripts/deterministic_workflow`
    # ("the runtime itself DEFINES the parameter") and never looked at the installed
    # mirror -- while its prose called itself an inventory of repository call sites and
    # said no production module supplies the argument.  Both halves of that were wrong:
    # production `standalone_preflight.run_preflight` does call
    # `check_auth(..., auth_probe_argv=...)`, and, far worse, a NEW production origin was
    # invisible.  The reviewer's mutation, applied to both engine trees --
    #
    #     scripts/deterministic_workflow/standalone_runtime.py
    #     -   auth_probe_argv = self.profile.auth_probe_argv()
    #     +   auth_probe_argv = ("os37-stub-cli", "auth", "status")
    #
    # -- replaced profile ownership with a hard-coded engine injection and the inventory
    # test still reported OK.
    #
    # The scope is now the WHOLE repository, split into two halves that are checked by
    # two different rules because the two halves mean different things:
    #
    #   (a) the ENGINE half -- `scripts/deterministic_workflow/**` AND the installed
    #       mirror `orca-worker-reviewer-orchestration/tools/deterministic_workflow/**`.
    #       Here the question is not "does a call pass the keyword" but WHERE A VALUE IS
    #       MANUFACTURED, so every occurrence of the identifier is classified and the
    #       classification is asserted against a declared table.  A function that hands a
    #       caller's value onward FORWARDS; a function that composes a value ORIGINATES,
    #       and exactly two sites are allowed to originate, both of them the profile's.
    #
    #   (b) the NON-ENGINE half -- every other `.py` / `.sh` under `scripts/`.  Here the
    #       engine's parameter is an OVERRIDE that a caller may legitimately exercise, so
    #       the question really is "which callers still pass it", and the answer is a
    #       declared allowlist.
    #
    # Nothing outside `scripts/` and the mirror engine can reach this parameter: the
    # mirror's tools tree is the only other copy of the runtime, and it is compared here
    # rather than assumed identical.
    ENGINE_TREES = ("scripts/deterministic_workflow",
                    "orca-worker-reviewer-orchestration/tools/deterministic_workflow")

    #: Kinds a classified occurrence can have.  `accessor` and `*-origin` MANUFACTURE a
    #: value for the bare name; `forward` hands the enclosing function's OWN parameter
    #: onward; `parameter` only receives; `read` inspects.  The distinction the reviewer
    #: asked for is exactly the line between the first group and the second.
    #:
    #: The remaining kinds CANNOT manufacture a value for the variable `run_preflight`
    #: reads, which is why they are not origins: `attribute-read` / `attribute-store` /
    #: `attribute-delete` touch an ATTRIBUTE of that name on some object, `mapping-key`
    #: and `string-reference` are the identifier appearing as a string, `annotation-only`
    #: is a bare `x: T` that binds nothing, `scope-declaration` is `global`/`nonlocal`
    #: (whose actual rebinding is classified where it is written), and `delete` unbinds.
    #: They are still recorded, and the exact-inventory assertion below refuses any new
    #: one -- that is what keeps "every occurrence is classified" honest without calling
    #: a non-manufacturing occurrence an origin.  Every form that DOES rebind the bare
    #: name lands in this first group, including via the fail-closed `Store` floor
    #: documented above `_classify_probe_sites`.
    ORIGIN_KINDS = frozenset({"accessor", "profile-origin", "literal-origin",
                              "other-origin"})

    #: EVERY occurrence of the identifier `auth_probe_argv` in an engine tree, with the
    #: number of times it occurs at that (module, function, kind).  Held as DATA and
    #: asserted, so a new engine site is an unaccounted entry rather than a stale
    #: sentence.  Both trees must produce this same table.
    PRODUCTION_PROBE_SITES = {
        ("standalone_profile.py", "StandaloneProfile.auth_probe_argv", "accessor"): 1,
        ("standalone_preflight.py", "check_auth", "parameter"): 1,
        ("standalone_preflight.py", "check_auth", "read"): 2,
        ("standalone_preflight.py", "run_preflight", "parameter"): 1,
        ("standalone_preflight.py", "run_preflight", "forward"): 1,
        ("standalone_runtime.py", "StandaloneSession.start", "parameter"): 1,
        ("standalone_runtime.py", "StandaloneSession.start", "read"): 1,
        ("standalone_runtime.py", "StandaloneSession.start", "profile-origin"): 1,
        ("standalone_runtime.py", "StandaloneSession.start", "forward"): 1,
        # `self.profile.auth_probe_argv` -- the identifier as an ATTRIBUTE, which is the
        # right-hand side of the `profile-origin` assignment above.  Recorded so the
        # inventory covers non-`Name` occurrences too; not an origin, because reading the
        # accessor off the profile is precisely what the profile is for.
        ("standalone_runtime.py", "StandaloneSession.start", "attribute-read"): 1,
    }

    #: The ONLY two engine sites permitted to MANUFACTURE a probe argv, each with the
    #: kind it must have.  `accessor` composes it from the profile's own binary and the
    #: profile's declared `AuthProbe` verb; `profile-origin` is an assignment whose value
    #: is a call to that accessor.  A `literal-origin` -- the reviewer's mutation -- is
    #: refused by name, and so is any other manufacture.
    ALLOWED_PRODUCTION_ORIGINS = {
        ("standalone_profile.py", "StandaloneProfile.auth_probe_argv"): (
            "accessor",
            "the one authority that composes an argv, and it composes it from THIS "
            "profile's binary plus this profile's declared AuthProbe verb"),
        ("standalone_runtime.py", "StandaloneSession.start"): (
            "profile-origin",
            "when the caller names no override the session reads the accessor; the "
            "value is the profile's, never the engine's own literal"),
    }

    #: Sites that FORWARD -- they pass the enclosing function's own parameter onward and
    #: manufacture nothing.  Iteration 2's prose denied that any production module passed
    #: the keyword; these two do, and forwarding is what they do.
    DECLARED_FORWARDING_SITES = {
        ("standalone_runtime.py", "StandaloneSession.start"):
            "hands its own `auth_probe_argv` parameter to `run_preflight`",
        ("standalone_preflight.py", "run_preflight"):
            "hands its own `auth_probe_argv` parameter to `check_auth`",
    }

    #: ---------------------------------------------------------------------------------
    #: ASSIGNMENT-ORIGIN COVERAGE -- the bound this classifier can actually back.
    #:
    #: Iteration 3's classifier handled `ast.Assign` and `ast.AnnAssign` only, while its
    #: docstring promised that EVERY occurrence was classified and that a new site of ANY
    #: kind became unaccounted.  The reviewer falsified that with one line added after the
    #: legitimate profile assignment in both engine copies of `StandaloneSession.start`:
    #:
    #:     (auth_probe_argv := ("os37-stub-cli", "auth", "status"))
    #:
    #: a real rebinding of the production variable that the inventory reported as `OK`.
    #: The lesson is not "add NamedExpr" -- it is that an ENUMERATION of forms can never
    #: be the thing the promise rests on, because the next form is always missing.  So
    #: coverage is now structural, in three layers:
    #:
    #:   L1  FAIL-CLOSED FLOOR.  Every Python binding of a BARE name produces an
    #:       `ast.Name` node whose `ctx` is `Store`.  `visit_Name` classifies a `Store`
    #:       occurrence that no enumerated form claimed as `other-origin` -- a
    #:       MANUFACTURING kind, refused by name.  An unenumerated or future binding form
    #:       therefore FAILS the inventory rather than passing it.  The enumeration below
    #:       exists to make the DIAGNOSIS precise (profile / literal / other), not to make
    #:       the coverage complete.
    #:
    #:   L2  THE ENUMERATED FORMS.  Every binding form in the Python grammar, each either
    #:       classified or explicitly declared impossible-here:
    #:
    #:         Assign                  `x = v`                classified from `v`
    #:         Assign, unpacking       `a, x = p, q`          element-wise when the shapes
    #:                                                        match, else `other-origin`;
    #:                                                        `*x` is always `other-origin`
    #:         AnnAssign with value    `x: T = v`             classified from `v`
    #:         AnnAssign, bare         `x: T`                 `annotation-only` -- binds
    #:                                                        nothing, only annotates
    #:         AugAssign               `x += v`               `other-origin`: the result is
    #:                                                        `old OP v`, never the
    #:                                                        accessor's return value
    #:         NamedExpr (walrus)      `(x := v)`             classified from `v`
    #:         For / AsyncFor target   `for x in it`          `other-origin`: an element of
    #:                                                        `it`, not a profile call
    #:         With / AsyncWith `as`   `with cm as x`         `other-origin`: a context
    #:                                                        manager's `__enter__` value
    #:         comprehension target    `[... for x in it]`    `other-origin`
    #:         except ... as           `except E as x`        `other-origin` (an exception)
    #:         Import / ImportFrom     `import x`, `as x`     `other-origin` (a module or
    #:                                                        an imported object)
    #:         FunctionDef / Async     `def x(...)`           `accessor` -- this IS the one
    #:                                                        authority, see below
    #:         Lambda parameters       `lambda x: ...`        `parameter` via `ast.arg`
    #:         ClassDef                `class x: ...`         `other-origin` (a class)
    #:         match patterns          `case ... as x`,       `other-origin`
    #:                                 `case [*x]`, `{**x}`
    #:         global / nonlocal       `global x`             `scope-declaration`: binds
    #:                                                        nothing itself; the REBINDING
    #:                                                        it enables is an `Assign` (or
    #:                                                        any form above) in that same
    #:                                                        scope and is classified there
    #:         del                     `del x`                `delete` (unbinds)
    #:         TypeAlias (3.12+)       `type x = ...`         `other-origin`
    #:         type params (PEP 695)   `def f[x]()`           `other-origin`
    #:         function parameter      `def f(x=...)`         `parameter` via `ast.arg`
    #:         walrus in a comprehension / decorator / default -- all `NamedExpr`, above
    #:
    #:       Forms that bind something OTHER than the bare name -- `obj.x = v` and
    #:       `d["x"] = v` -- do not rebind the variable `run_preflight` reads, so they are
    #:       recorded as the occurrences they are (`attribute-store`, and the
    #:       string-constant rules below) rather than as origins of the name.
    #:
    #:   L3  NON-`Name` OCCURRENCES, so "every occurrence" is literally true: an
    #:       `ast.Attribute` whose `attr` is the identifier (`attribute-read` /
    #:       `attribute-store` / `attribute-delete`), a dict-literal key (`mapping-key`),
    #:       and any other string constant exactly equal to the identifier
    #:       (`string-reference`).  Two string positions are REBINDINGS in disguise and are
    #:       classified as `other-origin` instead: a `Store`/`Del` subscript key
    #:       (`globals()["auth_probe_argv"] = ...`) and `setattr(obj, "auth_probe_argv", v)`.
    #:
    #: NAMED RESIDUAL, not a claim: a name built at runtime and bound through `exec`, a
    #: computed `globals()[name]`, or an import hook is invisible to any static reader,
    #: including this one.  That bound is stated, not papered over.  Nothing in either
    #: engine tree uses any of those, which the `string-reference` and `other-origin`
    #: rules above would surface as unaccounted entries if it ever did.

    @staticmethod
    def _classify_probe_sites(root: Path) -> dict[tuple[str, str, str], int]:
        """Classify every static occurrence of the identifier `auth_probe_argv`.

        Read from the AST, so a docstring paragraph about the parameter is not a site.
        `forward` is deliberately narrow: the keyword's value must be a bare name that is
        a PARAMETER of the enclosing function.  Anything else a call passes -- a literal,
        an attribute, a call result -- manufactures a value and is an origin.

        Coverage and its bound are defined in the comment block above this method.  The
        property relied on elsewhere is the fail-closed floor: a `Store` binding of the
        bare name that no enumerated form claimed is recorded as `other-origin`, so an
        unenumerated binding form fails the inventory instead of vanishing from it.
        """
        import ast

        NAME = "auth_probe_argv"

        def is_literal(node) -> bool:
            if isinstance(node, ast.Constant) or isinstance(node, ast.JoinedStr):
                return True
            if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                return all(is_literal(e) for e in node.elts)
            return False

        found: dict[tuple[str, str, str], int] = {}
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if NAME not in text:
                continue
            tree = ast.parse(text, filename=str(path))
            scope: list[str] = []
            params: list[frozenset[str]] = [frozenset()]
            keyword_values: set[int] = set()
            # `id()`s already accounted for, so the catch-alls do not double count.
            bound_targets: set[int] = set()      # Name nodes an enumerated form claimed
            counted_strings: set[int] = set()    # Constants a specific rule claimed

            def record(kind: str) -> None:
                key = (path.name, ".".join(scope) or "<module>", kind)
                found[key] = found.get(key, 0) + 1

            class Walk(ast.NodeVisitor):
                # -- scopes ------------------------------------------------------------
                def visit_ClassDef(self, node):
                    if node.name == NAME:
                        # `class auth_probe_argv:` binds a CLASS to the name.
                        record("other-origin")
                    self._type_params(node)
                    scope.append(node.name)
                    self.generic_visit(node)
                    scope.pop()

                def visit_FunctionDef(self, node):
                    scope.append(node.name)
                    if node.name == NAME:
                        record("accessor")
                    self._type_params(node)
                    a = node.args
                    names = {arg.arg for arg in
                             (*a.posonlyargs, *a.args, *a.kwonlyargs)}
                    params.append(frozenset(names))
                    self.generic_visit(node)
                    params.pop()
                    scope.pop()

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_Lambda(self, node):
                    # A lambda is its own parameter scope; without this frame a `forward`
                    # inside one would be judged against the enclosing function's params.
                    a = node.args
                    names = {arg.arg for arg in
                             (*a.posonlyargs, *a.args, *a.kwonlyargs)}
                    params.append(frozenset(names))
                    self.generic_visit(node)
                    params.pop()

                def _type_params(self, node) -> None:
                    # PEP 695 `def f[auth_probe_argv]()`; absent before 3.12.
                    for param in getattr(node, "type_params", ()) or ():
                        if getattr(param, "name", None) == NAME:
                            record("other-origin")

                def visit_arg(self, node):
                    if node.arg == NAME:
                        record("parameter")
                    self.generic_visit(node)

                # -- how a bound value is classified -----------------------------------
                def _manufactured(self, value) -> None:
                    if (isinstance(value, ast.Call)
                            and isinstance(value.func, ast.Attribute)
                            and value.func.attr == NAME):
                        record("profile-origin")
                    elif is_literal(value):
                        record("literal-origin")
                    else:
                        record("other-origin")

                def _bind(self, target, value, *, composed: bool = False,
                          kind: str | None = None) -> None:
                    """Record what `target` binds, descending through unpacking."""
                    if isinstance(target, ast.Starred):
                        # `*x` collects a list; never the accessor's own return value.
                        self._bind(target.value, None, composed=True)
                        return
                    if isinstance(target, (ast.Tuple, ast.List)):
                        elts = list(target.elts)
                        if (isinstance(value, (ast.Tuple, ast.List))
                                and len(value.elts) == len(elts)
                                and not any(isinstance(e, ast.Starred) for e in elts)):
                            for one, part in zip(elts, value.elts):
                                self._bind(one, part)
                        else:
                            for one in elts:
                                self._bind(one, None, composed=True)
                        return
                    if isinstance(target, ast.Name):
                        bound_targets.add(id(target))
                        if target.id != NAME:
                            return
                        if kind is not None:
                            record(kind)
                        elif composed or value is None:
                            record("other-origin")
                        else:
                            self._manufactured(value)
                        return
                    # An `Attribute` or `Subscript` target binds an attribute or an item,
                    # NOT the bare name; those occurrences are classified by
                    # `visit_Attribute` / `visit_Subscript` below.

                # -- the binding statement forms ---------------------------------------
                def visit_Assign(self, node):
                    for target in node.targets:
                        self._bind(target, node.value)
                    self.generic_visit(node)

                def visit_AnnAssign(self, node):
                    if node.value is None:
                        self._bind(node.target, None, kind="annotation-only")
                    else:
                        self._bind(node.target, node.value)
                    self.generic_visit(node)

                def visit_AugAssign(self, node):
                    self._bind(node.target, None, composed=True)
                    self.generic_visit(node)

                def visit_NamedExpr(self, node):
                    self._bind(node.target, node.value)
                    self.generic_visit(node)

                def visit_For(self, node):
                    self._bind(node.target, None, composed=True)
                    self.generic_visit(node)

                visit_AsyncFor = visit_For

                def visit_With(self, node):
                    for item in node.items:
                        if item.optional_vars is not None:
                            self._bind(item.optional_vars, None, composed=True)
                    self.generic_visit(node)

                visit_AsyncWith = visit_With

                def visit_comprehension(self, node):
                    self._bind(node.target, None, composed=True)
                    self.generic_visit(node)

                def visit_ExceptHandler(self, node):
                    if node.name == NAME:
                        record("other-origin")
                    self.generic_visit(node)

                def visit_Import(self, node):
                    for alias in node.names:
                        bound = alias.asname or alias.name.split(".")[0]
                        if bound == NAME:
                            record("other-origin")
                    self.generic_visit(node)

                visit_ImportFrom = visit_Import

                def visit_Global(self, node):
                    # Binds nothing on its own; the rebinding it PERMITS is an assignment
                    # form in this same scope and is classified where it is written.
                    if NAME in node.names:
                        record("scope-declaration")
                    self.generic_visit(node)

                visit_Nonlocal = visit_Global

                def visit_MatchAs(self, node):
                    if node.name == NAME:
                        record("other-origin")
                    self.generic_visit(node)

                def visit_MatchStar(self, node):
                    if node.name == NAME:
                        record("other-origin")
                    self.generic_visit(node)

                def visit_MatchMapping(self, node):
                    if node.rest == NAME:
                        record("other-origin")
                    self.generic_visit(node)

                def visit_TypeAlias(self, node):        # 3.12+; harmless earlier
                    self._bind(node.name, None, composed=True)
                    self.generic_visit(node)

                # -- every remaining occurrence ----------------------------------------
                def visit_Name(self, node):
                    if node.id != NAME:
                        return
                    if isinstance(node.ctx, ast.Load):
                        if id(node) not in keyword_values:
                            record("read")
                    elif isinstance(node.ctx, ast.Del):
                        record("delete")
                    elif id(node) not in bound_targets:
                        # THE FAIL-CLOSED FLOOR.  Some binding form this classifier does
                        # not enumerate put the name in `Store` context: treat it as a
                        # manufacture, which the "only the profile originates" assertion
                        # refuses.  A new or future form fails here instead of hiding.
                        record("other-origin")
                    self.generic_visit(node)

                def visit_Attribute(self, node):
                    if node.attr == NAME:
                        if isinstance(node.ctx, ast.Store):
                            record("attribute-store")
                        elif isinstance(node.ctx, ast.Del):
                            record("attribute-delete")
                        else:
                            record("attribute-read")
                    self.generic_visit(node)

                def visit_Dict(self, node):
                    for key in node.keys:
                        if (isinstance(key, ast.Constant)
                                and key.value == NAME):
                            counted_strings.add(id(key))
                            record("mapping-key")
                    self.generic_visit(node)

                def visit_Subscript(self, node):
                    key = node.slice
                    if isinstance(key, ast.Constant) and key.value == NAME:
                        counted_strings.add(id(key))
                        if isinstance(node.ctx, (ast.Store, ast.Del)):
                            # `globals()["auth_probe_argv"] = ...` really does rebind the
                            # module-level name, so it is a manufacture, not a reference.
                            record("other-origin")
                        else:
                            record("string-reference")
                    self.generic_visit(node)

                def visit_Call(self, node):
                    func = node.func
                    name = (func.attr if isinstance(func, ast.Attribute)
                            else func.id if isinstance(func, ast.Name) else "")
                    if (name in ("setattr", "delattr", "__setitem__", "__delitem__")
                            and len(node.args) >= 2
                            and isinstance(node.args[1], ast.Constant)
                            and node.args[1].value == NAME):
                        counted_strings.add(id(node.args[1]))
                        record("other-origin")
                    self.generic_visit(node)

                def visit_Constant(self, node):
                    if (node.value == NAME and isinstance(node.value, str)
                            and id(node) not in counted_strings):
                        record("string-reference")
                    self.generic_visit(node)

                def visit_keyword(self, node):
                    if node.arg == NAME:
                        value = node.value
                        keyword_values.add(id(value))
                        if (isinstance(value, ast.Name)
                                and value.id in params[-1]):
                            record("forward")
                        elif is_literal(value):
                            record("literal-origin")
                        else:
                            record("other-origin")
                    self.generic_visit(node)

            Walk().visit(tree)
        return found

    def test_the_engine_inventory_is_complete_and_only_the_profile_originates(self) -> None:
        """The corrected claim, over BOTH engine trees, classified rather than counted.

        WHAT IS PROMISED, stated as precisely as the code can back it.  Scope: every
        `.py` file under either engine tree, read as an AST.  Within that scope, every
        STATIC occurrence of the identifier `auth_probe_argv` is classified -- as a bare
        name in any context, as an attribute name, as a parameter or function name, and as
        a string constant equal to it.  Every Python form that can REBIND the bare name is
        classified as a manufacture unless it is demonstrably the profile accessor's own
        return value, and that does not rest on an enumeration of forms: a `Store`
        occurrence that no enumerated form claimed falls through to `other-origin`, which
        assertions (1)-(3) refuse.  Coverage, the enumerated forms, and the one named
        residual -- a name bound through `exec` or a computed `globals()` key, which no
        static reader can see -- are written out above `_classify_probe_sites`.

        Four assertions, ordered so the most specific one reports first.  (1) Every site
        that MANUFACTURES a value is one of the two declared origins.  (2) Each of those
        originates in the way it is declared to -- this is the line between FORWARDING a
        caller's value and MANUFACTURING one.  (3) Nothing manufactures a literal or
        anything else that is not the profile.  (4) The whole classified inventory is
        exactly the declared table, so a new engine occurrence of any classified kind --
        including the non-manufacturing kinds, which (1)-(3) deliberately ignore -- is
        unaccounted rather than invisible.

        (4) subsumes (2) and (3) only while the table itself is honest, which is precisely
        what a lazy fix would edit; (1)-(3) are what refuse the mutation even after the
        table has been updated to describe it.  Both were verified by applying the
        mutations, not by reasoning about them.

        Mutation-sensitivity, verified by applying each to BOTH engine trees and
        reverting; `evidence/mutation_proof.py` in this run's artifacts holds them as
        permanent, re-runnable entries.  Replacing
        `auth_probe_argv = self.profile.auth_probe_argv()` in `StandaloneSession.start`
        with a hard-coded `("os37-stub-cli", "auth", "status")` turns that site's
        `profile-origin` into a `literal-origin` and fails (2), (3) and (4).  Dropping
        `auth_probe_argv=auth_probe_argv` from `run_preflight`'s call to `check_auth`
        removes a forwarding site and fails (4).  Adding the reviewer's walrus rebinding
        `(auth_probe_argv := ("os37-stub-cli", "auth", "status"))` after the legitimate
        assignment -- the counterexample that defeated iteration 3 -- adds a second
        `literal-origin` in `StandaloneSession.start` and fails (2), (3) and (4).  So do
        the other rebinding shapes: `auth_probe_argv += (...)`,
        `_, auth_probe_argv = None, (...)`, `for auth_probe_argv in (...)`,
        `with ... as auth_probe_argv`, a comprehension target, and
        `globals()["auth_probe_argv"] = (...)`.
        """
        for tree in self.ENGINE_TREES:
            root = REPO / tree
            with self.subTest(tree=tree):
                self.assertTrue(root.is_dir(),
                                f"{tree} is missing; the inventory cannot be complete "
                                "while one of the two engine trees is unscanned")
                found = self._classify_probe_sites(root)
                # Every MANUFACTURING occurrence, kept as (site, kind) triples rather than
                # collapsed into a site->kind mapping.  A mapping silently loses the second
                # kind when one site manufactures in TWO ways, which is exactly the shape
                # of the walrus counterexample: a legitimate `profile-origin` with an
                # injected `literal-origin` beside it in the same function.  Sorting makes
                # which one reports first deterministic rather than dict-order luck.
                manufactured = sorted(key for key in found
                                      if key[2] in self.ORIGIN_KINDS)
                origins = {(module, qualname) for (module, qualname, _k) in manufactured}
                self.assertEqual(
                    origins, set(self.ALLOWED_PRODUCTION_ORIGINS),
                    "an engine site MANUFACTURES a probe argv and is not one of the two "
                    "declared origins; forwarding a caller's value is allowed anywhere, "
                    "manufacturing one is not: "
                    f"{sorted(origins - set(self.ALLOWED_PRODUCTION_ORIGINS))}")
                for module, qualname, kind in manufactured:
                    expected, why = self.ALLOWED_PRODUCTION_ORIGINS[(module, qualname)]
                    self.assertEqual(
                        kind, expected,
                        f"{module}::{qualname} is declared as `{expected}` ({why}) but "
                        f"now originates as `{kind}`; a hard-coded engine value has "
                        "replaced the profile as the owner of the probe")
                literals = sorted(key for key in found
                                  if key[2] in ("literal-origin", "other-origin"))
                self.assertEqual(
                    literals, [],
                    "the engine manufactures a probe argv from something that is not the "
                    f"profile: {literals}")
                self.assertEqual(
                    found, self.PRODUCTION_PROBE_SITES,
                    "the engine's `auth_probe_argv` sites are not the declared "
                    "inventory; a site was added, moved or removed, so either this is a "
                    "new injection or the inventory (and the report quoting it) is stale."
                    f"\n  unaccounted: {sorted(set(found) - set(self.PRODUCTION_PROBE_SITES))}"
                    f"\n  vanished:    {sorted(set(self.PRODUCTION_PROBE_SITES) - set(found))}")

    def test_both_engine_trees_carry_the_same_probe_inventory(self) -> None:
        """The installed mirror is not assumed identical -- it is classified too.

        An inventory of one tree says nothing about the copy an operator installs.  These
        are compared as classified inventories rather than as bytes, so a mirror that
        drifted only in this parameter still fails here.
        """
        inventories = {tree: self._classify_probe_sites(REPO / tree)
                       for tree in self.ENGINE_TREES}
        first, second = self.ENGINE_TREES
        self.assertEqual(inventories[first], inventories[second],
                         "the installed mirror's `auth_probe_argv` wiring differs from "
                         "the engine's; one of the two copies has an origin the other "
                         "does not")
        self.assertTrue(inventories[first],
                        "no engine site was classified at all; the scan found nothing, "
                        "which would make every other assertion here vacuous")

    #: The NON-ENGINE half.  These are `scripts/` modules OUTSIDE both engine trees that
    #: still pass `auth_probe_argv=` explicitly, with the reason each is legitimate.  The
    #: distinction that matters is not "does the string appear" but WHICH parameter it
    #: reaches: `check_auth(..., auth_probe_argv=...)` and `spawn_only(..., auth_probe_argv=
    #: ...)` are declared OVERRIDE parameters and exercising them is exercising the
    #: contract.  What finding #6 forbids is a caller SUBSTITUTING for the profile --
    #: supplying the probe because nothing in the composition could -- and after the fix
    #: no non-test module does.
    DECLARED_PROBE_OVERRIDES = {
        "test_os37_cli_preconditions.py": "exercises `check_auth` / `spawn_only`'s declared "
                                          "override parameter, including its refusal legs",
        "test_os37_capability_honesty.py": "supplies the whole `_start_kwargs` seam, of "
                                           "which the probe override is one member",
        "test_os37_external_review_regressions.py": "this module: asserts the override "
                                                    "contract and this very inventory",
    }

    #: Modules that must supply NO probe at all -- the profile is the only supplier.
    NON_TEST_MODULES_THAT_MUST_NOT_INJECT = ("os37_r10_real_agent.py",
                                             "os37_r10_standalone_e2e.sh")

    def test_the_only_non_engine_callers_that_supply_the_probe_are_declared_overrides(self) -> None:
        """The non-engine half of the inventory, asserted rather than asserted-in-prose.

        Scope: every `.py` / `.sh` under `scripts/` that is NOT inside an engine tree.
        The engine trees are excluded HERE because they are covered by
        `test_the_engine_inventory_is_complete_and_only_the_profile_originates` under a
        stricter rule -- not because the runtime is exempt, which is what iteration 2's
        version effectively assumed.

        Two halves.  (a) No harness module supplies `auth_probe_argv` -- the R10
        real-agent harness's injection is gone and stays gone.  (b) The test modules that
        still pass it are exactly the enumerated ones, each exercising the declared
        override parameter, so a NEW injection shows up as an unaccounted file rather than
        quietly contradicting the report.

        Mutation-sensitivity: put the harness's `auth_probe_argv=[...]` back into
        `scripts/os37_r10_real_agent.py` and this fails by name.
        """
        engine = REPO / "scripts" / "deterministic_workflow"
        offenders: list[str] = []
        unaccounted: list[str] = []
        for path in sorted((REPO / "scripts").rglob("*")):
            if not path.is_file() or path.suffix not in (".py", ".sh"):
                continue
            if path.is_relative_to(engine):
                continue                      # covered by the engine inventory above
            if not self._supplies_the_probe(path):
                continue                      # a COMMENT about it is not a supply
            if path.name in self.NON_TEST_MODULES_THAT_MUST_NOT_INJECT:
                offenders.append(f"{path.name} supplies `auth_probe_argv` below the "
                                 "production entry point again")
            elif path.name not in self.DECLARED_PROBE_OVERRIDES:
                unaccounted.append(path.name)
        self.assertEqual(offenders, [], "\n".join(offenders))
        self.assertEqual(
            unaccounted, [],
            "a module passes `auth_probe_argv` and is not in the declared inventory; either "
            "it is a new injection or the inventory (and the report that quotes it) is "
            "stale: " + ", ".join(unaccounted))
        # And the enumerated modules really do still pass it -- an inventory listing files
        # that no longer do would be a claim about nothing.
        for name in self.DECLARED_PROBE_OVERRIDES:
            with self.subTest(module=name):
                matches = [p for p in (REPO / "scripts").rglob(name)
                           if not p.is_relative_to(engine)]
                self.assertTrue(matches, f"{name} is gone; the inventory is stale")
                self.assertTrue(self._supplies_the_probe(matches[0]),
                                f"{name} no longer passes the override; drop it from the "
                                "inventory rather than leaving a claim about nothing")

    @staticmethod
    def _supplies_the_probe(path: Path) -> bool:
        """Does this file really SUPPLY `auth_probe_argv`, as opposed to mentioning it?

        Read from the AST, so the R10 harness's COMMENT saying the injection was removed
        does not count as an injection, and a docstring paragraph about the parameter does
        not either.

        R3-01 applies here too, in the same direction: iteration 3 looked for an
        `ast.keyword` or a dict-LITERAL key, and a module could have supplied the probe
        past both -- `kwargs = {}; kwargs["auth_probe_argv"] = [...]; start(**kwargs)`
        builds the mapping one subscript at a time and contains neither.  The question
        this predicate answers is coarser than the engine classifier's ("does this file
        supply the probe at all", not "where is the value manufactured"), so the fix is to
        widen it to the coarsest honest reading: the identifier appearing as a STRING, in
        any position, plus the keyword form.  The identifier is specific enough that a
        string equal to it is a supply or a claim about one, and the positive half of the
        caller's assertion keeps the allowlist from going stale either way.

        Bound, stated rather than implied: a supply assembled from a COMPUTED key
        (`kwargs["auth_probe" + "_argv"]`, or a name read from a file) is invisible here,
        as it is to any static reader.  No module under `scripts/` does that today.
        """
        import ast
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix != ".py":
            return "auth_probe_argv=" in text or '"auth_probe_argv"' in text
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:                              # pragma: no cover - all parse
            return "auth_probe_argv" in text
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "auth_probe_argv":
                return True
            # Any string constant equal to the identifier: a dict-literal key, a subscript
            # key, a `setattr`/`getattr` name, a `**{...}` built elsewhere.
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value == "auth_probe_argv"):
                return True
        return False

    def test_a_profile_declaring_no_probe_is_refused_never_passed(self) -> None:
        """G-2's branch is unchanged: unknown is not a pass."""
        from scripts.deterministic_workflow import standalone_preflight as preflight
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree, auth_probe=None)
        self.assertIsNone(profile.auth_probe_argv())
        outcome = preflight.check_auth(profile, {}, auth_probe_argv=None)
        self.assertEqual(outcome["verdict"], "unknown")
        self.assertFalse(preflight.compose([outcome])["proceed"])


# =====================================================================================
# F7 -- one closed, validated credential contract
# =====================================================================================
class F07CredentialContractTests(unittest.TestCase):
    """A profile could declare `CLAUDE_CODE_OAUTH_TOKEN` and then be refused at spawn."""

    def _profile(self, **overrides) -> StandaloneProfile:
        fields = dict(driver="claude", binary="claude",
                      supported_range=((1, 0, 0), (9, 0, 0)),
                      delivery_mode="launch_with_prompt", identity_binding="adopted")
        fields.update(overrides)
        return StandaloneProfile(**fields)

    def test_an_explicit_oauth_token_profile_builds_a_clean_child_environment(self) -> None:
        """The reproduction: the CLI's own documented credential variable.

        `FORBIDDEN_CHILD_ENV_PREFIXES` contains `CLAUDE`, and `ALLOWED_EXCEPTIONS` was a
        separate literal that did not carry `CLAUDE_CODE_OAUTH_TOKEN` -- so an explicit,
        correct configuration was accepted at construction and then RAISED
        `ChildEnvironmentLeak` at spawn, naming the operator's own variable as a leak.
        """
        profile = self._profile(auth_secret_ref={"CLAUDE_CODE_OAUTH_TOKEN": "SRC_TOKEN"})
        env = env_policy.build_child_env(
            profile, parent_env={"PATH": "/usr/bin", "HOME": "/tmp"},
            secret_resolver=lambda ref: "s3cret" if ref == "SRC_TOKEN" else "")
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "s3cret")
        env_policy.assert_clean(env)                      # raises on a leak

    def test_the_allowed_names_are_one_closed_set_shared_by_both_modules(self) -> None:
        """Two lists is how this happened; there is now one, and it is DERIVED."""
        from scripts.deterministic_workflow import standalone_profile as profile_mod
        self.assertTrue(
            profile_mod.ALLOWED_CREDENTIAL_NAMES <= env_policy.ALLOWED_EXCEPTIONS,
            "the environment allowlist does not admit every name the profile contract "
            "does; a profile can be valid and unspawnable again")
        self.assertEqual(
            env_policy.ALLOWED_EXCEPTIONS,
            profile_mod.ALLOWED_CREDENTIAL_NAMES | profile_mod.ALLOWED_CONFIG_ROOT_NAMES
            | frozenset({env_policy.SPAWN_TOKEN_ENV}),
            "the allowlist is a restated literal again rather than the derived set")

    def test_a_name_outside_the_contract_is_refused_at_profile_construction(self) -> None:
        """Closed means closed: the session's own bearer token is still refused."""
        for name in ("CLAUDE_CODE_MESSAGING_TOKEN", "ORCA_AGENT_HOOK_TOKEN",
                     "CLAUDE_CODE_MESSAGING_SOCKET"):
            with self.subTest(name=name):
                with self.assertRaises(CredentialContractViolation):
                    self._profile(auth_secret_ref={name: "SRC"})

    def test_a_credential_may_not_be_declared_as_a_plain_driver_env_value(self) -> None:
        """`driver_env` carries VALUES inside a committed profile."""
        with self.assertRaises(CredentialContractViolation):
            self._profile(driver_env={"CLAUDE_CODE_OAUTH_TOKEN": "s3cret"})

    def test_the_value_never_appears_in_a_journal_a_log_or_an_artifact(self) -> None:
        """Names travel; values do not.  Asserted on every reporting surface."""
        profile = self._profile(auth_secret_ref={"CLAUDE_CODE_OAUTH_TOKEN": "SRC_TOKEN"})
        env = env_policy.build_child_env(
            profile, parent_env={"PATH": "/usr/bin", "HOME": "/tmp"},
            secret_resolver=lambda ref: "s3cret" if ref == "SRC_TOKEN" else "")
        for surface in (json.dumps(profile.redacted()),
                        json.dumps(env_policy.describe(env)),
                        env_policy.env_digest(env)):
            with self.subTest(surface=surface[:40]):
                self.assertNotIn("s3cret", surface, "a credential VALUE was published")
                self.assertNotIn("SRC_TOKEN", surface,
                                 "the secret REFERENCE was published; it names a real "
                                 "credential location")
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", profile.redacted()["auth_secret_env_names"])

    def test_login_update_and_permission_prompts_stay_fail_closed(self) -> None:
        """The prompts must remain failures, not a pass bought by the wider allowlist."""
        from scripts.deterministic_workflow import standalone_preflight as preflight
        profile = self._profile(auth_secret_ref={"CLAUDE_CODE_OAUTH_TOKEN": "SRC_TOKEN"},
                                auth_probe=AuthProbe(args=("auth", "status")))
        env = {"CLAUDE_CODE_OAUTH_TOKEN": "s3cret"}
        for mode, reason in (("interactive", "auth_probe_interactive"),
                             ("timeout", "auth_probe_unreadable"),
                             ("unreadable", "auth_probe_unreadable")):
            with self.subTest(mode=mode):
                outcome = preflight.check_auth(
                    profile, env, auth_probe_argv=list(profile.auth_probe_argv()),
                    prober=lambda argv, e, *, timeout_ms, cwd=None, _m=mode: {
                        "outcome": _m, "exit_code": 0, "output": "Please log in"})
                self.assertEqual(outcome["verdict"], "fail")
                self.assertEqual(outcome["reason"], reason)


# =====================================================================================
# F8 -- a dispatch failure is a settlement, not a traceback
# =====================================================================================
class F08DispatchFailureIsSettledTests(_ProductionPath):
    """`StandaloneDispatchFailed` escaped `adapter.start` and killed the whole graph."""

    def _unsatisfiable_profile(self) -> StandaloneProfile:
        """A CLI that emits NOTHING: readiness can never close.  A real auth expiry, an
        OOM kill and a crash all reach the same place -- a dispatch with no verdict."""
        stream = self.base / "silent.stream"
        stream.write_text("", encoding="utf-8")
        return replay_profile(
            stream=stream, exit_code=0, worktree=self.worktree,
            timeouts=Timeouts(preflight_timeout_ms=5_000, readiness_timeout_ms=1_000,
                              delivery_verify_timeout_ms=1_000,
                              completion_timeout_ms=2_000))

    def test_a_non_completing_dispatch_produces_a_typed_failed_settlement(self) -> None:
        """`adapter.start` must RETURN a settlement, not raise through the graph.

        Mutation-sensitivity: remove the handler in `StandaloneAdapter.start` and this
        raises `StandaloneDispatchFailed` instead of asserting.
        """
        profile = self._unsatisfiable_profile()
        adapter, _state, ledger = self.compose(profile, run_id="run_fail")
        intent = self.intent("intent-fail", run_id="run_fail")
        receipt, event = self.dispatch(adapter, ledger, intent)
        self.assertTrue(receipt["settled"])
        self.assertEqual(receipt["outcome"], "failed")
        self.assertTrue(receipt["failure_stage"],
                        "the failure stage is unnamed; an operator learns nothing")
        self.assertIsNotNone(event, "the engine's immediate settlement read finds nothing")
        contracts.validate_event(intent, dict(event))

    def test_the_executor_settles_it_instead_of_propagating_a_traceback(self) -> None:
        """Through `executor._settle_now` -- the actual boundary the finding names."""
        from scripts.deterministic_workflow import executor
        profile = self._unsatisfiable_profile()
        adapter, _state, ledger = self.compose(profile, run_id="run_exec")
        intent = self.intent("intent-exec", run_id="run_exec")
        claim = ledger.claim(intent)
        try:
            event = executor._settle_now(adapter, ledger, intent, claim["lease_token"])
        except BaseException as exc:                # noqa: BLE001 - the ESCAPE is the point
            raise AssertionError(
                f"the dispatch escaped `executor._settle_now` as {type(exc).__name__}: "
                f"{exc}; this is the exact boundary finding #8 names -- the executor has no "
                "handler, so an escape here takes the whole run with it") from None
        self.assertEqual(event["result"].get("status"), "BLOCKED",
                         "`_settle_now` did not reach a typed FAILED settlement; the "
                         f"dispatch failure was not converted into a verdict: {event!r}")

    def test_a_reviewer_failure_routes_to_the_workflow_correction_path(self) -> None:
        """The typed failure uses the WORKFLOW's own vocabulary, so routing decides.

        A failed PHASE_REVIEWER dispatch settles `result=FAIL`, and `routing.phase_gate`
        already sends that to `PREPARE_CORRECTION`.  No CLI-specific branch exists in
        `routing.py`, and none is needed -- which is asserted here by driving the REAL
        router with the state a failed reviewer settlement produces.
        """
        from scripts.deterministic_workflow import routing
        profile = self._unsatisfiable_profile()
        adapter, _state, ledger = self.compose(profile, run_id="run_rev")
        intent = self.intent("intent-rev", role="PHASE_REVIEWER", run_id="run_rev")
        _receipt, event = self.dispatch(adapter, ledger, intent)
        self.assertEqual(event["result"].get("result"), "FAIL",
                         "a failed reviewer dispatch does not carry the workflow's own "
                         f"FAIL verdict, so routing has nothing to act on: {event!r}")
        contracts.validate_event(intent, dict(event))
        state = {
            "decision_state": "CLEAR", "round_kind": "PHASE_GATE",
            "current_phase": "IMPLEMENTATION", "risk": "high",
            "worker_result": {"status": "COMPLETE", "unit_test_status": "PASS"},
            "reviewer_result": dict(event["result"]),
            "remaining_phase_budget": {"IMPLEMENTATION": 3},
            "final_review_iterations": 0, "max_iterations": 5,
            "adapter_capabilities": sorted(contracts.BASE_CAPABILITIES),
            "correction_queue": [], "pending_gate_defect": None,
            "run_lifecycle": "RUNNING", "pause_binding": None,
        }
        self.assertEqual(routing.phase_gate(state), "FAIL")
        self.assertEqual(routing.route(state), "PREPARE_CORRECTION",
                         "a failed reviewer dispatch does not reach the correction path")


# =====================================================================================
# F9 -- two journals, two responsibilities
# =====================================================================================
class F09LifecycleJournalWiringTests(_ProductionPath):
    """`ExecutionJournal` was supplied where `FileSettlementJournal` was required."""

    def test_the_composition_root_wires_both_journals_to_their_own_interface(self) -> None:
        """`row()`/`record()` on one, `open_dispatches()`/`axes_for()` on the other.

        Mutation-sensitivity: pass the `ExecutionJournal` as `pause_row_journal` and the
        `row()` call below raises `AttributeError` -- which is exactly the exception the
        graph's broad handler was converting into `DISPATCH_UNACCOUNTED`.
        """
        from scripts.deterministic_workflow.pause_store import FileSettlementJournal
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        adapter, _state, _ledger = self.compose(profile, run_id="run_journals")
        self.assertIsInstance(adapter.settlement_journal, journal_mod.ExecutionJournal)
        self.assertIsInstance(adapter.pause_row_journal, FileSettlementJournal)
        self.assertIsNone(adapter.pause_row_journal.row("nothing-here"))
        adapter.pause_row_journal.record("intent-j", stage="PLANNED",
                                         run_id="run_journals")
        self.assertEqual(adapter.pause_row_journal.row("intent-j")["stage"], "PLANNED")
        self.assertEqual(adapter.settlement_journal.open_dispatches(), ())
        for absent in ("row", "record"):
            self.assertFalse(
                hasattr(adapter.settlement_journal, absent),
                f"ExecutionJournal grew a {absent}() shim; the two responsibilities must "
                "stay distinguishable rather than being duck-typed together")

    def test_the_composition_root_hands_the_row_journal_to_the_graph(self) -> None:
        """`run_cli` passes it explicitly; `graph.py` is a pinned policy module."""
        import inspect
        source = inspect.getsource(launcher.run_cli)
        self.assertIn("pause_row_journal", source)
        self.assertIn('graph_extras["journal"]', source,
                      "the composition root no longer names which journal it means")
        # Read as SOURCE, never imported: `graph.py` imports `langgraph` at module scope
        # and this assertion must hold in the dependency-ABSENT lane too, where importing
        # it raises `ModuleNotFoundError`.  The claim is about the bytes anyway.
        graph_source = (REPO / "scripts" / "deterministic_workflow" / "graph.py").read_text(
            encoding="utf-8")
        self.assertNotIn("pause_row_journal", graph_source,
                         "graph.py gained a standalone-aware branch; it is a pinned "
                         "policy module and this ticket adds none")


# =====================================================================================
# F10 -- the capability snapshot, and the post-receipt restart
# =====================================================================================
class F10ExternalResumeIsArmedTests(_ProductionPath):
    """`external_resume` was snapshotted before the ledger existed, so it was never declared."""

    def test_a_crash_after_the_receipt_is_collected_and_the_effect_is_not_re_run(self) -> None:
        """The consequence, end to end, through the recovery ladder itself.

        The window finding #10 makes unrecoverable is: the receipt is durable (an `execve`
        provably happened) and no settlement is.  A successor process must OBSERVE that
        effect, never re-create it -- and `executor._collect` can only do that when the
        adapter DECLARES `external_resume`.  With the capability withdrawn, the ladder
        refuses `IDEMPOTENCY_RECOVERY_UNSUPPORTED` and the effect is stranded forever.

        Mutation-sensitivity: take the capability snapshot before the ledger again and the
        refusal code changes from BLOCKED (an observation) to UNSUPPORTED (a dead end).
        """
        from scripts.deterministic_workflow import executor
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        ledger = InMemoryRuntimeStateStore()
        adapter, state, _ = self.compose(profile, run_id="run_resume", ledger=ledger)
        self.assertIn(contracts.EXTERNAL_RESUME, state["adapter_capabilities"],
                      "the run's frozen declaration omits external_resume, so no effect an "
                      "earlier process created can ever be collected")

        intent = self.intent("intent-resume", run_id="run_resume")
        claim = ledger.claim(intent)
        # The crash window, produced rather than described: `spawn_only` is the adapter's
        # own non-blocking verb and it writes exactly the one durable receipt `start`
        # writes, then returns without settling.  That IS "the process died after the
        # receipt".
        adapter.spawn_only(intent, lease_token=claim["lease_token"], payload="work")
        stored = ledger.get_receipt("intent-resume")
        self.assertEqual(stored["status"], "EFFECTED")
        self.assertTrue((stored.get("receipt") or {}).get("external_id"))
        spawns_before = self._spawn_count("run_resume", "intent-resume")
        self.assertEqual(spawns_before, 1)

        # A STRANGER process: a new adapter over the same durable files, holding none of
        # the first one's objects.
        successor, _state2, _ = self.compose(profile, run_id="run_resume", ledger=ledger)
        with self.assertRaises(executor.IdempotencyRecoveryError) as refused:
            executor._recover(successor, ledger, intent, dict(stored),
                              claim["lease_token"])
        self.assertEqual(
            refused.exception.code, "IDEMPOTENCY_RECOVERY_BLOCKED",
            "the ladder did not even reach `resume`; with external_resume undeclared it "
            "refuses IDEMPOTENCY_RECOVERY_UNSUPPORTED and the effect is unrecoverable")
        self.assertEqual(self._spawn_count("run_resume", "intent-resume"), spawns_before,
                         "the successor re-ran an effect that already existed")

    def test_a_settled_effect_is_collected_rather_than_re_run(self) -> None:
        """The other half: once it HAS settled, the successor harvests the verdict."""
        from scripts.deterministic_workflow import executor
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        ledger = InMemoryRuntimeStateStore()
        adapter, _state, _ = self.compose(profile, run_id="run_collect", ledger=ledger)
        intent = self.intent("intent-collect", run_id="run_collect")
        claim = ledger.claim(intent)
        adapter.start(intent, lease_token=claim["lease_token"])
        spawns = self._spawn_count("run_collect", "intent-collect")

        successor, _s2, _ = self.compose(profile, run_id="run_collect", ledger=ledger)
        stored = ledger.get_receipt("intent-collect")
        collected = executor._recover(successor, ledger, intent, dict(stored),
                                      claim["lease_token"])
        self.assertIsNotNone(collected)
        self.assertEqual(self._spawn_count("run_collect", "intent-collect"), spawns,
                         "the successor re-ran a settled effect")

    def _spawn_count(self, run_id: str, intent_id: str) -> int:
        journal = journal_mod.ExecutionJournal(self.base, run_id)
        return len([row for row in journal.rows_for(intent_id)
                    if row["kind"] == "SPAWN_OBSERVED"])

    def test_the_ledger_less_composition_is_refused_by_name(self) -> None:
        """The ordering is structural, not a comment."""
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        with self.assertRaises(launcher.LauncherError) as refused:
            launcher.build_standalone_adapter(
                {"run_id": "run_noledger", "thread_id": "t",
                 "phases": ["IMPLEMENTATION"]},
                artifact_base=self.base, run_id="run_noledger",
                profile_spec=profile_spec(profile))
        self.assertIn(launcher.STANDALONE_ADAPTER_REQUIRES_LEDGER, str(refused.exception))


# =====================================================================================
# F11 -- a refusal is not a transition
# =====================================================================================
class F11RefusedInterruptTests(_ProductionPath):
    """A refused interrupt appended an `exit_unproven` observation and poisoned the axes."""

    def test_a_refused_interrupt_changes_no_state_no_log_and_no_axis(self) -> None:
        """A TRANSIENTLY unreadable process table must leave nothing behind.

        The ownership gate refuses (no signal is sent, no lifecycle edge is taken), but the
        method used to journal an `exit_unproven` record whose axes said
        `process_liveness=unverifiable` -- and `axes_for` reads the LAST record carrying
        axes, so one unreadable read permanently replaced a healthy dispatch's liveness with
        ignorance and fed the ownership BLOCK of finding #5.

        Mutation-sensitivity: restore the unconditional journal write and BOTH the axes
        assertion and the event-log assertion fail.
        """
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        adapter, _state, ledger = self.compose(profile, run_id="run_interrupt")
        intent = self.intent("intent-interrupt", run_id="run_interrupt")
        session = adapter.runtime.session_for(intent)
        session.record = {"pid": 424242, "pgid": 424242, "sid": 424242,
                          "captured_tty": "ttys999", "session_id": session.session_id,
                          "process_incarnation": session.incarnation,
                          "created_by_this_runtime": True, "host_scope": "local",
                          "user_taken_over": False, "resource_kind": "pty_session",
                          "spawn_token": session.spawn_token}
        # A HEALTHY prior observation, so the test can show it survives the refusal.
        session._journal(kind="EVENT", derived_from="pty", event="spawned",
                         state="STARTING",
                         axes=session._axes(settlement="not_settled",
                                            worker_resource="retain",
                                            process_liveness="live",
                                            cleanup_authority="not_authorized"),
                         vocabulary={"pty_id": "pty-1", "pid": 424242,
                                     "captured_tty": "ttys999",
                                     "session_digest": "digest-1",
                                     **session._terminal_provenance()})
        before_axes = dict(session.journal.axes_for(session.intent_id))
        before_state, before_log = session.state, list(session.event_log)

        # The process table cannot be read THIS ONCE.  Ownership is refused.
        session._table_reader = lambda tty: {"tty": tty, "captured_at": 0.0, "rows": (),
                                             "readable": False}
        result = session.interrupt("stop")

        self.assertEqual(result["interrupt_outcome"], "not_owned",
                         "the gate did not refuse, so this proves nothing")
        self.assertEqual(session.state, before_state, "a refusal moved the state")
        self.assertEqual(session.event_log, before_log,
                         "a refusal appended to the transition log")
        self.assertEqual(dict(session.journal.axes_for(session.intent_id)), before_axes,
                         "a refused interrupt overwrote the latest ownership axes")
        self.assertEqual(before_axes["process_liveness"], "live",
                         "the fixture's healthy observation was not the latest to begin "
                         "with, so this assertion would pass vacuously")

        # It is still RECORDED -- "nothing happened" and "we never asked" differ.
        refusals = [row for row in session.journal.rows_for(session.intent_id)
                    if row["kind"] == "REFUSED"]
        self.assertTrue(refusals, "the refusal left no audit trail at all")
        self.assertEqual(refusals[-1]["event"], "",
                         "the audit record carries a lifecycle event again")
        self.assertEqual(refusals[-1]["source_vocabulary"]["refusal"],
                         "refusal_is_not_a_transition")

    def test_a_refused_interrupt_does_not_make_the_row_block_the_pause(self) -> None:
        """The finding's own consequence: this feeds #5's ownership BLOCK."""
        from scripts.deterministic_workflow import executor
        profile = replay_profile(stream=STREAMS / "m14_claude_genuine_turn.stream",
                                 exit_code=0, worktree=self.worktree)
        adapter, _state, ledger = self.compose(profile, run_id="run_intpause")
        intent = self.intent("intent-intpause", run_id="run_intpause")
        session = adapter.runtime.session_for(intent)
        adapter._journal_planned(intent, session)
        # A REAL spawn, for the reason F5's case above gives: `recover_handle` now verifies
        # the row against the live process table and the run's own exit sentinel
        # (finding 8), so a fabricated pid is an orphan it correctly refuses.  The
        # interrupt itself is still driven over an UNREADABLE table -- that is the refusal
        # this case is about -- through the session's own injectable reader.
        claim = ledger.claim(intent)
        spawned = adapter.spawn_only(intent, lease_token=claim["lease_token"],
                                     payload="work")
        self.assertEqual(spawned["start_outcome"], "ready", spawned)
        session._table_reader = lambda tty: {"tty": tty, "captured_at": 0.0, "rows": (),
                                             "readable": False}
        session.interrupt("stop")
        row = executor._settlement_row(adapter, adapter.pause_row_journal,
                                       "intent-intpause", now="1970-01-01T00:00:00Z")
        self.assertIn(row["terminal_disposition"],
                      pause_policy.AC1_DISCHARGING_DISPOSITIONS,
                      "a refused interrupt still blocks the pause")


# =====================================================================================
# F12 -- the skip manifest, bound per test
# =====================================================================================
class F12SkipManifestBindingTests(unittest.TestCase):
    """The anti-drift check accepted any `skipUnless`/`skipIf` ANYWHERE in a module."""

    def test_every_manifest_entry_is_bound_to_its_declared_test(self) -> None:
        """All 67 entries, every condition -- not only the `always` ones."""
        alternatives = ci_lane.load_tolerated_alternatives()
        self.assertTrue(alternatives)
        unbound = [(condition, test_id, reason)
                   for test_id, entries in alternatives.items()
                   for condition, reason in entries
                   if not ci_lane.skip_guard_binds(test_id, reason)]
        self.assertEqual(unbound, [],
                         "a manifest entry names a test that is not guarded by a skip "
                         "carrying that reason")

    def test_the_module_wide_search_the_old_check_used_is_not_sufficient(self) -> None:
        """The strengthening, demonstrated on a real pair.

        `test_review_isolation` carries BOTH gates, on different classes.  A module-wide
        search cannot tell them apart; a per-test binding must.
        """
        darwin = ("the seatbelt backend is darwin-only; T-8.9 carries the fail-closed "
                  "guarantee on every other platform")
        sandbox_only = ("test_review_isolation.ProfileRenderingTests"
                        ".test_t86f_a_generated_profile_actually_parses")
        both = ("test_review_isolation.NegativeContractTests"
                ".test_neg2_the_sandboxed_process_cannot_open_the_key")
        self.assertTrue(ci_lane.skip_guard_binds(both, darwin))
        self.assertFalse(
            ci_lane.skip_guard_binds(sandbox_only, darwin),
            "a gate on another class of the same module was accepted as this test's")
        source = (REPO / "scripts" / "test_review_isolation.py").read_text(encoding="utf-8")
        self.assertIn(darwin.split(";")[0], source,
                      "the module DOES contain the reason, which is exactly why a "
                      "module-wide search could not tell these two apart")

    def test_an_f_string_reason_binds_by_its_literal_segments(self) -> None:
        """`NEEDS_SANDBOX`'s reason has no literal form in the source at all."""
        sandbox = ("test_review_isolation.ProfileRenderingTests"
                   ".test_t86f_a_generated_profile_actually_parses")
        self.assertTrue(
            ci_lane.skip_guard_binds(sandbox,
                                     "/usr/bin/sandbox-exec is not present on this host"))
        self.assertFalse(
            ci_lane.skip_guard_binds(sandbox,
                                     "/usr/bin/sandbox-exec is present on this host"),
            "the literal segments of an f-string reason are not matched exactly")

    def test_a_renamed_or_deleted_test_fails_the_binding(self) -> None:
        for test_id in ("test_orca_runtime.OrcaRuntimeIntegrationTests.test_gone",
                        "test_orca_runtime.NoSuchClass.test_runtime_scenarios"):
            with self.subTest(test_id=test_id):
                self.assertFalse(
                    ci_lane.skip_guard_binds(
                        test_id, "requires --orca-runtime and a ready Orca runtime"))


if __name__ == "__main__":                                # pragma: no cover
    unittest.main()


# =====================================================================================
# THE PRODUCTION PATH, END TO END -- external review iteration-2 finding B-03
# =====================================================================================
#
# Everything above this line composes the adapter the way `run_cli` composes it and then
# dispatches through `adapter.start` (or, for a few structural claims, through a private
# executor helper).  The iteration-1 review's verdict on that was correct and it is not
# argued with here: **injection below the composition root does not discharge a finding.**
# `build_standalone_adapter` is not the entry point an operator runs; `run_workflow.py` is,
# and between the two sit the graph, the routing policy, the decision gate, the correction
# loop and the pause/dispose nodes -- every one of which the findings above are supposed to
# be safe for.
#
# So each finding below is exercised a SECOND time, through
#
#     launcher.run_cli(["--adapter", "standalone", "--state", ..., "--standalone-profile",
#                       ..., "--artifact-base", ..., "--runtime-state", ...,
#                       "--checkpoint-store", ..., "--json"])
#
# which is byte-for-byte the argument vector `orca-worker-reviewer-orchestration/tools/
# run_workflow.py` parses, reaching the real LangGraph graph, the real routing policy and a
# real settlement or a real correction round.  Nothing is stubbed, monkeypatched or injected
# anywhere in these tests: the only things they choose are the operator's own JSON files.
#
# The agent is `scripts/fixtures/os37/bin/os37-graph-agent`, compiled to a NATIVE executable
# (R-A leg 4 is an executable-IMAGE identity) and steered per run by profile `driver_env`
# alone -- the same channel an operator has.  It binds its answer to the intent it was
# handed, so the decision gate's binding rules are genuinely satisfied rather than bypassed.
#
# These runs spawn real local processes and take seconds each, which is the price of the
# claim.  They are gated on the pinned LangGraph runtime and on nothing else: a missing C
# compiler RAISES rather than skipping, because a fixture that cannot be built is not a pass.


def _langgraph_ok() -> bool:
    import importlib.metadata
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:   # pragma: no cover - present lane
        return False


#: Declared in `scripts/langgraph_skip_manifest.txt` for every class below.
LANGGRAPH_REASON = "requires pinned langgraph 0.2.76"

#: The non-secret fixture credential, and the environment name the profile RESOLVES it
#: through.  The value is asserted absent from every artifact the run writes (finding #7).
GRAPH_CREDENTIAL_ENV = "OS37_GA_CREDENTIAL"
GRAPH_CREDENTIAL_VALUE = "os37-graph-agent-non-secret-fixture-credential"


def _graph_agent_dir() -> Path:
    from scripts import os37_graph_agent_fixture as graph_fixture
    built = graph_fixture.native_agent_dir()
    if built is None:                                 # pragma: no cover - CI has cc
        raise AssertionError(graph_fixture.NO_COMPILER_REASON)
    return built


class GraphRun:
    """Everything one real `run_workflow.py --adapter standalone` run left behind."""

    def __init__(self, *, exit_code: int, summary: dict, escaped: BaseException | None,
                 base: Path, artifact_base: Path, run_id: str, ledger_path: Path,
                 checkpoint_path: Path, intents: Path, argv_dump: Path, env_dump: Path,
                 stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.summary = summary
        self.escaped = escaped
        self.base = base
        self.artifact_base = artifact_base
        self.run_id = run_id
        self.ledger_path = ledger_path
        self.checkpoint_path = checkpoint_path
        self.intents_dir = intents
        self.argv_dump = argv_dump
        self.env_dump = env_dump
        self.stdout = stdout
        self.stderr = stderr

    # -- the durable evidence, read exactly as a stranger process would ----------------
    def journal_rows(self) -> list[dict]:
        journal = journal_mod.ExecutionJournal(self.artifact_base, self.run_id)
        return list(journal.rows())

    def settlement_rows(self) -> list[dict]:
        return [row for row in self.journal_rows() if row["kind"] == "SETTLEMENT_OBSERVED"]

    def spawn_rows(self) -> list[dict]:
        return [row for row in self.journal_rows() if row["kind"] == "SPAWN_OBSERVED"]

    def delivered_intents(self) -> list[dict]:
        if not self.intents_dir.is_dir():
            return []
        return [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(self.intents_dir.glob("*.json"))]

    def probe_argv(self) -> list[str]:
        if not self.argv_dump.is_file():
            return []
        return [line for line in self.argv_dump.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def child_environments(self) -> list[dict[str, str]]:
        if not self.env_dump.is_dir():
            return []
        dumps = []
        for path in sorted(self.env_dump.glob("*.env")):
            names = {}
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                name, _, value = line.partition("=")
                if name and "=" in line:
                    names[name] = value
            dumps.append(names)
        return dumps

    def artifact_files(self) -> list[Path]:
        return [path for path in self.artifact_base.rglob("*") if path.is_file()]


def graph_profile_document(*, worktree: Path, driver_env: dict[str, str],
                           auth_probe: bool = True, credential: bool = True,
                           preflight_ms: int = 1_500,
                           timeouts: dict[str, int] | None = None) -> dict:
    """The JSON an operator writes for `--standalone-profile`.  Nothing else.

    `preflight_ms` is sized for a local fixture on a loopback pty, not for a hosted model:
    preflight's two rehearsals each spawn the agent and wait out this budget, because the
    fixture -- like a real CLI -- waits for a prompt rather than exiting, so it dominates
    the cost of a dispatch.  It is a PROFILE field precisely so a caller may declare its own.
    """
    document = {
        "driver": "claude", "binary": "os37-graph-agent",
        "supported_range": [[1, 0, 0], [3, 0, 0]],
        "bin_dirs": [str(_graph_agent_dir())],
        "worktree": str(worktree),
        "readiness_records": [{"channel": "structured", "record_type": "system",
                               "session_field": "session_id"}],
        "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
        "identity_flag": "--session-id",
        "delivery_proofs": [{"channel": "structured", "record_type": "assistant"}],
        # The CONJUNCTIVE completion predicate finding #2 is about, declared in full: the
        # error field, the success field and its admissible values.
        "completion_records": [{"channel": "structured", "record_type": "result",
                                "error_field": "is_error",
                                "success_field": "terminal_reason",
                                "success_values": ["completed"]}],
        # Finding #3's selector: the report is a FIELD of the completion record (the shape
        # both installed CLIs emit), so without this the whole JSON stream would go to the
        # Markdown parser.  Declared as PROFILE DATA, with no CLI name anywhere in code.
        "result_body_records": [{"channel": "structured", "record_type": "result",
                                 "body_field": "result"}],
        "driver_env": dict(driver_env),
        "timeouts": {"preflight_timeout_ms": preflight_ms,
                     "readiness_timeout_ms": 20_000,
                     "delivery_verify_timeout_ms": 10_000, **(timeouts or {})},
    }
    if auth_probe:
        document["auth_probe"] = {"args": ["auth", "status"]}
    if credential:
        document["auth_secret_ref"] = {"ANTHROPIC_API_KEY": GRAPH_CREDENTIAL_ENV}
    return document


def execute_graph_cli(room: Path, *, run_id: str, phases: tuple[str, ...] = ("DESIGN",),
                      driver_env: dict[str, str] | None = None, auth_probe: bool = True,
                      credential: bool = True, timeouts: dict[str, int] | None = None,
                      preflight_ms: int = 1_500, max_iterations: int = 4,
                      approval_authority: str = "", thread_id: str = "graph",
                      decision_state: str = "",
                      decision_reason_code: str | None = None,
                      checkpoint_name: str = "checkpoints.json") -> GraphRun:
    """`run_workflow.py --adapter standalone ...`, in this process, and nothing else.

    Re-invoking with the SAME `room` re-enters that run's durable files -- the same artifact
    base, the same ledger and the same checkpoint store -- which is what a restart is.

    `approval_authority` is R4's `--approval-authority`.  The default is the EMPTY STRING,
    not `"none"`, so a caller that does not name one produces exactly the argv this helper
    produced before R4 existed -- the flag is absent from the command line entirely, and
    every existing case here therefore still exercises `build_parser`'s own default rather
    than a value this helper chose for it.
    """
    import contextlib

    artifact_base = room / "artifact_base"
    artifact_base.mkdir(parents=True, exist_ok=True)
    worktree = room / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    intents = room / "intents"
    argv_dump = room / "argv.txt"
    env_dump = room / "child_env"
    declared = {"OS37_GA_INTENT_DUMP": str(intents), "OS37_GA_ARGV_DUMP": str(argv_dump),
                "OS37_GA_ENV_DUMP": str(env_dump),
                # The agent emits its report INSIDE the completion record, which is what
                # makes the profile's `result_body_records` selector load-bearing.
                "OS37_GA_BODY_IN_RESULT": "1", **(driver_env or {})}
    profile = graph_profile_document(worktree=worktree, driver_env=declared,
                                     auth_probe=auth_probe, credential=credential,
                                     preflight_ms=preflight_ms, timeouts=timeouts)
    state = {"run_id": run_id, "thread_id": thread_id, "phases": list(phases),
             "risk": "high", "max_iterations": max_iterations}
    if decision_state:
        # R4.  The launch specification's own optional field; omitted entirely when no
        # caller names one, so every existing case writes the same state.json as before.
        state["decision_state"] = decision_state
        state["decision_reason_code"] = decision_reason_code
    (room / "profile.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
    (room / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    # The credential is resolved BY REFERENCE out of the environment, which is the only
    # thing the profile may name (finding #7).  It is restored on the way out.
    previous = os.environ.get(GRAPH_CREDENTIAL_ENV)
    os.environ[GRAPH_CREDENTIAL_ENV] = GRAPH_CREDENTIAL_VALUE
    out, err = io.StringIO(), io.StringIO()
    escaped: BaseException | None = None
    code = -1
    argv = ["--adapter", "standalone",
            "--state", str(room / "state.json"),
            "--standalone-profile", str(room / "profile.json"),
            "--artifact-base", str(artifact_base),
            "--runtime-state", str(room / "ledger.json"),
            "--checkpoint-store", str(room / checkpoint_name),
            "--project-root", str(REPO), "--json"]
    if approval_authority:
        argv += ["--approval-authority", approval_authority]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = launcher.run_cli(argv)
    except BaseException as exc:                # noqa: BLE001 - an ESCAPE is the finding
        escaped = exc
    finally:
        if previous is None:
            os.environ.pop(GRAPH_CREDENTIAL_ENV, None)
        else:
            os.environ[GRAPH_CREDENTIAL_ENV] = previous
    summary: dict = {}
    for line in reversed(out.getvalue().strip().splitlines()):
        try:
            summary = json.loads(line)
            break
        except ValueError:
            continue
    return GraphRun(exit_code=code, summary=summary, escaped=escaped, base=room,
                    artifact_base=artifact_base, run_id=run_id,
                    ledger_path=room / "ledger.json",
                    checkpoint_path=room / checkpoint_name, intents=intents,
                    argv_dump=argv_dump, env_dump=env_dump,
                    stdout=out.getvalue(), stderr=err.getvalue())


class _GraphAssertions:
    """Assertions every end-to-end case shares.  A mixin, so each class states its own gate."""

    def assert_nothing_escaped(self, run: GraphRun) -> None:
        if run.escaped is not None:
            raise AssertionError(
                f"the run escaped `run_cli` as {type(run.escaped).__name__}: "
                f"{run.escaped!s}; a dispatch outcome must be a settlement the workflow can "
                f"route on, never a traceback out of the graph")

    def assert_reached_a_terminal(self, run: GraphRun) -> None:
        self.assert_nothing_escaped(run)
        self.assertEqual(run.summary.get("run_lifecycle"), "SETTLED",
                         f"the graph did not settle: {run.summary!r}\n{run.stderr}")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class E2ECompletedRunTests(_GraphAssertions, unittest.TestCase):
    """ONE real completed run of the graph, and the four findings it settles at once.

    F3, F6, F7 and F9 are four properties of the SAME dispatch, so they are asserted from
    one run rather than from four: the run is real, it costs real seconds, and splitting it
    would buy nothing but wall clock.  Each assertion is its own case and names its finding.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.room = Path(tempfile.mkdtemp(prefix="os37-e2e-ok-"))
        cls.graph = execute_graph_cli(cls.room, run_id="run_e2eok")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.room, ignore_errors=True)

    def test_the_run_completes_through_the_real_cli_and_graph(self) -> None:
        """The precondition every other case here rests on, asserted rather than assumed."""
        self.assert_reached_a_terminal(self.graph)
        self.assertEqual(self.graph.summary.get("terminal_status"), "COMPLETED",
                         f"the standalone graph run did not complete: "
                         f"{self.graph.summary!r}\n{self.graph.stderr}")
        self.assertEqual(self.graph.exit_code, 0)
        self.assertGreaterEqual(len(self.graph.spawn_rows()), 3,
                                "fewer than three real agent processes were spawned, so "
                                "Worker, phase Reviewer and Final Review cannot all have run")

    def test_f3_the_extracted_body_reaches_the_shared_parser_through_the_graph(self) -> None:
        """F3, at the graph.  A body lost inside its stream cannot pass the decision gate.

        The agent writes its report AFTER the structured `result` record, so the transcript
        the runtime captures is JSON lines followed by Markdown.  Before the fix the WHOLE
        transcript went to `decision_contract.parse_agent_settlement`, which then found no
        `STATUS:` line and no `decision-gate` fence -- and OS-42's gate refuses that as a
        FORM defect.  A run that reaches `WORKFLOW_COMPLETED` therefore proves the extracted
        body reached the shared parser: the gate accepted a record bound to THIS run.

        Mutation-sensitivity: hand the whole transcript to the parser again and the run
        terminates `DECISION_GATE_REPAIR_EXHAUSTED` instead of `WORKFLOW_COMPLETED`.
        """
        self.assert_reached_a_terminal(self.graph)
        self.assertEqual((self.graph.summary.get("terminal_reason") or {}).get("code"),
                         "WORKFLOW_COMPLETED",
                         "the graph did not accept a bound decision-gate record from the "
                         "agent's report, which is what an unextracted body looks like")
        settled = self.graph.settlement_rows()
        self.assertTrue(settled, "no dispatch settled at all")
        for row in settled:
            with self.subTest(intent=row["intent_id"]):
                self.assertEqual((row["source_vocabulary"] or {}).get("result_body_source"),
                                 "result.result",
                                 "the settlement was parsed from the raw transcript rather "
                                 "than from the profile-declared `result_body_records` "
                                 "selector, which is exactly finding #3")
        # And the record the gate accepted really is bound to THIS run, not to the
        # fixture's own canonical run id -- an unbound record is refused by OS-42, so this
        # is what makes the acceptance above evidence of extraction.
        delivered = self.graph.delivered_intents()
        self.assertTrue(delivered, "the agent recorded no delivered intent")
        self.assertEqual({intent["run_id"] for intent in delivered}, {"run_e2eok"})

    def test_f6_the_declared_auth_probe_is_run_at_the_process_boundary(self) -> None:
        """F6, at the graph.  The argv is READ OFF THE CHILD, never injected by this test.

        The finding is that only a caller constructing a `StandaloneSession` directly could
        supply `auth_probe_argv`, so nothing the launcher composed ever probed at all.  Here
        the probe is declared in the operator's profile JSON, the run goes through
        `run_cli`, and the evidence is a line the AGENT ITSELF appended when the kernel
        executed it.

        Mutation-sensitivity: stop reading `profile.auth_probe_argv()` in
        `StandaloneSession.start` and no `auth status` invocation appears here.
        """
        self.assert_reached_a_terminal(self.graph)
        invocations = self.graph.probe_argv()
        self.assertTrue(invocations, "the agent recorded no invocation at all")
        probes = [line for line in invocations if "auth" in line.split()]
        self.assertTrue(
            probes,
            "the profile declares `auth_probe: {args: [auth, status]}` and the composition "
            "root never ran it; the argv observed at the process boundary was:\n"
            + "\n".join(invocations[:20]))
        for line in probes:
            with self.subTest(argv=line):
                self.assertEqual(line.split()[:2], ["auth", "status"],
                                 "the probe argv is not the one the profile declared")

    def test_f7_the_child_environment_is_the_closed_credential_contract(self) -> None:
        """F7, at the graph.  Observed in the CHILD, and the value is in no artifact.

        Two halves, and neither is satisfiable by the other's fix: (a) every environment the
        run really handed a child carries the declared credential NAME and no name outside
        the closed contract, and (b) the credential VALUE appears in no file the run wrote --
        not the journal, not the ledger, not the capture logs, not the checkpoint store.
        """
        self.assert_reached_a_terminal(self.graph)
        dumps = self.graph.child_environments()
        self.assertTrue(dumps, "no child environment was observed at the process boundary")
        for names in dumps:
            with self.subTest(pid_env=sorted(names)[:5]):
                self.assertEqual(names.get("ANTHROPIC_API_KEY"), GRAPH_CREDENTIAL_VALUE,
                                 "the declared credential did not resolve into the child")
                leaked = sorted(name for name in names
                                if name.upper().startswith(("CLAUDE", "CODEX", "ORCA",
                                                            "ANTHROPIC"))
                                and name not in env_policy.ALLOWED_EXCEPTIONS)
                self.assertEqual(leaked, [],
                                 "a name outside the closed credential/config contract "
                                 "reached the child environment")
        leaks = [str(path.relative_to(self.graph.artifact_base))
                 for path in self.graph.artifact_files()
                 if GRAPH_CREDENTIAL_VALUE.encode() in path.read_bytes()]
        self.assertEqual(leaks, [],
                         "the credential VALUE was written into a run artifact")
        for path in (self.graph.ledger_path, self.graph.checkpoint_path):
            with self.subTest(durable=path.name):
                self.assertNotIn(GRAPH_CREDENTIAL_VALUE,
                                 path.read_text(encoding="utf-8", errors="replace"))

    def test_f9_the_pause_rows_the_engine_reads_exist_for_every_dispatch(self) -> None:
        """F9, at the graph.  The row journal the PAUSE/DISPOSE nodes read, from a real run.

        `graph.build_graph` falls back to `adapter.settlement_journal` -- the append-only
        `ExecutionJournal`, which has neither `row()` nor `record()` -- unless the
        composition root says otherwise, and the resulting `AttributeError` was swallowed
        into `DISPATCH_UNACCOUNTED`.  So the check is not "the launcher passes `journal=`";
        it is that after a real run the store the engine reads HAS a row for every dispatch
        the run made, read back through `pause_store`'s own reader.

        Mutation-sensitivity: wire `pause_row_journal=journal` (the execution journal) in
        `build_standalone_adapter` and this run leaves no rows in the pause store at all.
        """
        self.assert_reached_a_terminal(self.graph)
        from scripts.deterministic_workflow import pause_store
        # Resolved by the store's OWN locator, never by a path this test spells out: the
        # engine's PAUSE and DISPOSE nodes reach it exactly this way, and hard-coding the
        # layout here would let the two drift while this case stayed green.
        store = pause_store.journal_for(self.graph.run_id,
                                        artifact_base=self.graph.artifact_base)
        dispatched = {row["intent_id"] for row in self.graph.spawn_rows()}
        self.assertTrue(dispatched, "the run spawned nothing")
        for intent_id in sorted(dispatched):
            with self.subTest(intent=intent_id):
                row = store.row(intent_id)
                self.assertIsNotNone(
                    row, "the engine's pause-row store holds no row for a dispatch this "
                         "run really made, so every pause over it reports "
                         "DISPATCH_UNACCOUNTED")
                self.assertEqual(row["run_id"], self.graph.run_id,
                                 "the row carries no run binding; `_settlement_row` fails "
                                 "at its first `journal.record(...)`")
                self.assertEqual(row["terminal_origin"], "standalone_pty")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class E2EAuthFailureIsNeverSuccessTests(_GraphAssertions, unittest.TestCase):
    """F2, at the graph.  A measured authentication failure, through the real CLI."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.room = Path(tempfile.mkdtemp(prefix="os37-e2e-auth-"))
        cls.graph = execute_graph_cli(cls.room, run_id="run_e2eauth",
                                      driver_env={"OS37_GA_AUTH_FAIL": "1"})

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.room, ignore_errors=True)

    def test_an_authenticated_failure_never_reaches_a_completed_workflow(self) -> None:
        """The agent emits the measured M-15 shape and exits 1; the run must NOT succeed.

        `is_error=true`, `terminal_reason="api_error"` and `rc=1` -- three independent legs
        of the profile's conjunctive predicate, any one of which refuses.  Before the fix
        `await_completion` accepted "a completion record exists AND an exit was proven" and
        `_settle` wrote `outcome=succeeded` unconditionally, so this run would have reported
        a COMPLETED workflow over an unauthenticated agent.

        Mutation-sensitivity: hard-code `outcome = "succeeded"` in `_settle` and this run
        terminates COMPLETED with exit code 0.
        """
        self.assert_reached_a_terminal(self.graph)
        self.assertNotEqual(self.graph.summary.get("terminal_status"), "COMPLETED",
                            "an authentication failure produced a COMPLETED workflow")
        self.assertNotEqual(self.graph.exit_code, 0,
                            "the CLI reported success for a run whose only agent turn "
                            "failed to authenticate")

    def test_every_settlement_of_the_run_is_a_typed_failure(self) -> None:
        """And the durable record says so, per dispatch, in the workflow's own vocabulary."""
        self.assert_reached_a_terminal(self.graph)
        settled = self.graph.settlement_rows()
        self.assertTrue(settled, "an auth failure produced no settlement at all")
        self.assertEqual(sorted({row["outcome"] for row in settled}), ["failed"],
                         "a dispatch that failed to authenticate was journalled as a success")
        self.assertEqual(sorted({row["state"] for row in settled}), ["FAILED"])
        for row in settled:
            with self.subTest(intent=row["intent_id"]):
                verdict = (row["source_vocabulary"] or {}).get("completion_verdict") or {}
                self.assertEqual(verdict.get("outcome"), "failed")
                self.assertIn(verdict.get("reason"),
                              ("error_field_set", "terminal_reason_not_success",
                               "exit_code_nonzero"),
                              "the refusing leg is not named, so an operator cannot see "
                              f"which one refused: {verdict!r}")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class E2ELongTurnCompletesTests(_GraphAssertions, unittest.TestCase):
    """F4, at the graph.  An agent turn that outruns the READINESS window still completes."""

    #: The turn takes ~3s; the readiness window is 1.2s.  Before finding #4's fix
    #: `await_completion` waited under `readiness_timeout_ms`, so a turn longer than the
    #: bound on *becoming ready* was declared LOST while the agent was still working.
    READINESS_MS = 1_200
    TURN_MS = 3_000

    @classmethod
    def setUpClass(cls) -> None:
        cls.room = Path(tempfile.mkdtemp(prefix="os37-e2e-slow-"))
        cls.graph = execute_graph_cli(
            cls.room, run_id="run_e2eslow",
            driver_env={"OS37_GA_TURN_DELAY_MS": str(cls.TURN_MS)},
            timeouts={"readiness_timeout_ms": cls.READINESS_MS,
                      "completion_timeout_ms": 60_000})

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.room, ignore_errors=True)

    def test_a_turn_longer_than_the_readiness_window_completes_the_whole_run(self) -> None:
        """Every dispatch of the run takes longer than the readiness bound, and all settle.

        Mutation-sensitivity: point `await_completion`'s deadline back at
        `readiness_timeout_ms` and every dispatch here is settled `failed` with a lost exit,
        because the agent is still mid-turn when the window closes.
        """
        self.assert_reached_a_terminal(self.graph)
        self.assertLess(self.READINESS_MS, self.TURN_MS,
                        "this case is vacuous unless the turn outruns the readiness window")
        self.assertEqual(self.graph.summary.get("terminal_status"), "COMPLETED",
                         f"a healthy but slow agent did not complete the run: "
                         f"{self.graph.summary!r}\n{self.graph.stderr}")
        settled = self.graph.settlement_rows()
        self.assertTrue(settled)
        self.assertEqual(sorted({row["outcome"] for row in settled}), ["succeeded"],
                         "a turn slower than the readiness window was settled as a failure")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class E2EDispatchFailureRoutesToCorrectionTests(_GraphAssertions, unittest.TestCase):
    """F8, at the graph.  A non-completing dispatch becomes a verdict the workflow routes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.room = Path(tempfile.mkdtemp(prefix="os37-e2e-fail-"))
        cls.graph = execute_graph_cli(
            cls.room, run_id="run_e2efail",
            # The FIRST phase-reviewer dispatch produces a delivery proof and then dies with
            # no completion record -- a real `StandaloneDispatchFailed` -- and every later
            # turn behaves normally, so the run can go on to reach the correction route.
            driver_env={"OS37_GA_NO_RESULT_ONCE": str(cls.room / "once"),
                        "OS37_GA_NO_RESULT_ROLE": "PHASE_REVIEWER"},
            timeouts={"completion_timeout_ms": 4_000})

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.room, ignore_errors=True)

    def test_the_failure_does_not_escape_the_graph_as_a_traceback(self) -> None:
        """The finding itself: `StandaloneDispatchFailed` used to kill the whole run.

        `executor._settle_now` calls `adapter.start` with no handler, so a readiness
        timeout, an auth expiry, an OOM kill or a missing result propagated through every
        graph node and out of `run_cli`.  This asserts the observable consequence: the CLI
        RETURNS, and it returns a terminal the workflow decided on.

        Mutation-sensitivity: re-raise instead of calling `settle_failed` in
        `StandaloneAdapter.start` and this fails with the escaped exception named.
        """
        self.assert_nothing_escaped(self.graph)
        self.assertEqual(self.graph.summary.get("run_lifecycle"), "SETTLED",
                         f"the run did not settle: {self.graph.summary!r}")

    def test_the_non_completing_dispatch_is_a_typed_failed_settlement(self) -> None:
        """It is a VERDICT, in the engine's own vocabulary, not an absence."""
        self.assert_nothing_escaped(self.graph)
        failed = [row for row in self.graph.settlement_rows() if row["outcome"] == "failed"]
        self.assertTrue(
            failed,
            "the dispatch that produced no completion record left no failed settlement; "
            "there is then nothing for the workflow to route on")
        for row in failed:
            with self.subTest(intent=row["intent_id"]):
                self.assertEqual(row["state"], "FAILED")
                verdict = (row["source_vocabulary"] or {}).get("completion_verdict") or {}
                self.assertTrue(verdict.get("stage"),
                                "the failure names no stage, so an operator cannot see "
                                "where the dispatch died")
                event = (row["source_vocabulary"] or {}).get("event") or {}
                result = event.get("result") or {}
                self.assertEqual(result.get("result"), "FAIL",
                                 "a failed REVIEWER dispatch must carry the reviewer "
                                 "vocabulary's FAIL; that is the engine's own verdict "
                                 "vocabulary, not a standalone one")
                self.assertEqual(result.get("standalone_failure", {}).get("stage"),
                                 verdict.get("stage"),
                                 "the settlement result does not name the stage the "
                                 "dispatch died at")

    def test_the_engine_routes_on_the_typed_failure_with_no_standalone_branch(self) -> None:
        """And the workflow ACTS on the verdict -- observed at the process boundary.

        A dispatch that died before producing a report also produced no decision-gate
        declaration, so the FIRST policy to see the typed FAILED settlement is OS-42's
        bounded validation-repair loop: it re-dispatches the same role with
        `repair_attempt=1` and a `repair_instruction` naming `DECISION_GATE_INPUT_MISSING`.
        That is the honest route for THIS input and it is stated as such -- the correction
        route proper (`PREPARE_CORRECTION`) is reached by a well-formed reviewer FAIL and is
        exercised end to end by :class:`E2ECorrectionRoundTests`.

        What matters for finding #8 is the same either way: the failure became a verdict the
        engine's OWN policy decided on, with no standalone branch anywhere above
        `standalone_*`, instead of a traceback out of the graph.
        """
        self.assert_nothing_escaped(self.graph)
        delivered = self.graph.delivered_intents()
        self.assertTrue(delivered, "the agent recorded no delivered intent")
        repairs = [intent for intent in delivered
                   if int(intent.get("repair_attempt") or 0) >= 1]
        self.assertTrue(
            repairs,
            "nothing was re-dispatched after the dispatch failed, so the typed FAILED "
            "settlement reached no policy at all. delivered: "
            + repr([(i.get("role"), i.get("phase"), i.get("repair_attempt"))
                    for i in delivered]))
        for intent in repairs:
            with self.subTest(intent=intent["intent_id"]):
                instruction = intent.get("repair_instruction") or {}
                codes = [defect.get("code") for defect in instruction.get("defects") or []]
                self.assertIn("DECISION_GATE_INPUT_MISSING", codes,
                              "the re-dispatch is not the engine's repair round")
        self.assertEqual(self.graph.summary.get("run_lifecycle"), "SETTLED")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class E2ECorrectionRoundTests(_GraphAssertions, unittest.TestCase):
    """F8's other half, and F5's row, at the graph: a real reviewer FAIL -> correction.

    `routing.phase_gate` is a PINNED policy module and this ticket adds no branch to it, so
    the proof that a standalone run reaches the correction loop has to be a real run: a
    phase Reviewer returns FAIL with a well-formed, bound gate record, and the engine
    dispatches a correction WORKER round for the same phase at the next phase iteration.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.room = Path(tempfile.mkdtemp(prefix="os37-e2e-corr-"))
        cls.graph = execute_graph_cli(cls.room, run_id="run_e2ecorr",
                                      driver_env={"OS37_GA_FAIL_PHASE": "DESIGN"})

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.room, ignore_errors=True)

    def test_a_reviewer_fail_dispatches_a_correction_round_for_the_same_phase(self) -> None:
        """The correction route, end to end, with no CLI-specific branch above `standalone_*`."""
        self.assert_reached_a_terminal(self.graph)
        delivered = self.graph.delivered_intents()
        corrections = [intent for intent in delivered
                       if intent.get("role") == "WORKER"
                       and intent.get("phase") == "DESIGN"
                       and int(intent.get("phase_iteration") or 0) >= 2]
        self.assertTrue(
            corrections,
            "a phase Reviewer FAIL dispatched no correction round. delivered: "
            + repr([(i.get("role"), i.get("phase"), i.get("phase_iteration"))
                    for i in delivered]))
        self.assertEqual(self.graph.summary.get("terminal_status"), "COMPLETED",
                         "the run did not recover through the correction loop")
        self.assertGreaterEqual(
            (self.graph.summary.get("phase_iterations") or {}).get("DESIGN", 0), 2,
            "the run reports no second phase iteration, so no correction happened")

    def test_no_workflow_or_review_module_reads_the_standalone_vocabulary(self) -> None:
        """The structural half of the same claim, so the run above cannot be a coincidence.

        Mutation-sensitivity: add any `standalone`/`delivery_mode`/`profile` mention to one
        of these pinned policy modules and this fails by name.
        """
        import ast
        pinned = ("routing.py", "graph.py", "executor.py", "state.py", "pause_policy.py",
                  "ports.py")
        engine = REPO / "scripts" / "deterministic_workflow"
        offenders: list[str] = []
        for name in pinned:
            source = (engine / name).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and "standalone" in node.id.lower():
                    offenders.append(f"{name}: name {node.id}")
                if isinstance(node, ast.Attribute) and "standalone" in node.attr.lower():
                    offenders.append(f"{name}: attribute .{node.attr}")
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and node.value in ("delivery_mode", "standalone", "profile")):
                    offenders.append(f"{name}: literal {node.value!r}")
        self.assertEqual(
            offenders, [],
            "a pinned workflow/decision/review policy module branches on the standalone "
            "runtime's vocabulary:\n" + "\n".join(offenders))


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class E2EPostReceiptRestartIsFencedTests(_GraphAssertions, unittest.TestCase):
    """F10 / iteration-2 finding B-01.  The identity fence, with its EFFECT locked.

    The iteration-1 F10 cases locked the ARMING of the fence -- that
    `build_standalone_adapter` refuses a ledger-less composition and that
    `external_resume` is really in the run's frozen capability declaration -- and the
    reviewer was right that they locked nothing about the fence's effect: an early
    `return stored` inserted into `StandaloneAdapter.resume` before the row comparison left
    all three of them green.  Re-checking that showed WHY: `resume` was never even reached
    with a stored settlement.  ``executor._recover`` asks ``adapter.settlement`` FIRST and
    harvests whatever it answers, and that answer was UNFENCED, so the comparison inside
    ``resume`` could not be reached through the production ladder at all.  The fence was
    dead code.  It is now applied where the ladder actually reads -- see
    ``StandaloneAdapter.settlement`` -- and this case drives the whole thing through the
    real CLI.

    **The crash window is produced, not described, and nothing is hand-edited.**  A real run
    settles a real dispatch; then a successor ledger is built through the runtime-state
    PORT'S OWN PUBLIC API (`claim` -> `record_receipt` -> `release`) carrying a REAL receipt
    from a REAL `execve` and no settlement.  That is exactly what a process that died
    between `journal.admit(...)` and `runtime_state.settle(...)` leaves behind.

    **The window is FAITHFUL** (consolidated follow-up review, finding 7).  Every OTHER
    intent the first run settled is carried into the successor ledger exactly as the first
    ledger holds it -- `claim` -> `record_receipt` -> `settle`, the same public API -- so
    the successor holds what a Coordinator that crashed at that instant would hold: the
    settlements it had already written, and the one receipt it had not yet settled.  The
    earlier version handed the successor a ledger holding ONLY the target receipt, and the
    positive half then reached COMPLETED only because the reviewer intent -- already
    executed and settled by the first run under an incarnation the successor held no
    receipt for -- was re-dispatched, refused `IDEMPOTENCY_RECOVERY_BLOCKED` at `start`,
    had its failed settlement REFUSED by the journal (`SETTLEMENT_IDENTITY_MISMATCH`), and
    was then written to the ledger anyway as a reviewer FAIL that drove a correction round.
    That silent refusal is finding 7; with it fixed the same run stops as a typed BLOCKED
    terminal, and this case would have been green only by way of the defect.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.room = Path(tempfile.mkdtemp(prefix="os37-e2e-fence-"))
        cls.first = execute_graph_cli(cls.room, run_id="run_e2efence")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.room, ignore_errors=True)

    # -- the durable facts the first run left, read as a stranger process would ---------
    def _first_worker_row(self) -> dict:
        settled = [row for row in self.first.settlement_rows() if row["outcome"] == "succeeded"]
        self.assertTrue(settled, f"the first run settled nothing: {self.first.summary!r}")
        return settled[0]

    def _fence_of(self, row: dict) -> str:
        return f"{row['session_id']}:{row['process_incarnation']}"

    def _spawns_for(self, run: GraphRun, intent_id: str) -> int:
        return len([row for row in run.spawn_rows() if row["intent_id"] == intent_id])

    def _restart_with_receipt(self, *, intent_id: str, external_id: str,
                              tag: str) -> GraphRun:
        """Re-enter the SAME run root with a successor ledger in the crash window."""
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        original = FileRuntimeStateStore(self.first.ledger_path)
        record = original.get_receipt(intent_id)
        self.assertIsNotNone(record, "the first run recorded no ledger record")
        self.assertEqual(record["status"], "SETTLED",
                         "the first run did not settle this dispatch, so there is no "
                         "post-receipt window to restart into")
        receipt = dict(record["receipt"])
        receipt["external_id"] = external_id

        successor_path = self.room / f"ledger_{tag}.json"
        successor = FileRuntimeStateStore(successor_path)

        def intent_of(stored: dict) -> dict:
            return {key: stored[key] for key in
                    ("intent_id", "command_id", "payload_digest", "run_id", "phase",
                     "role", "round_kind")}

        # Every OTHER settled intent, carried over through the public API: what the
        # crashed Coordinator had already written before it died.
        for other_id, other in sorted(original._read().items()):
            if other_id == intent_id or other.get("status") != "SETTLED":
                continue
            other_claim = successor.claim(intent_of(other))
            successor.record_receipt(other_id, dict(other["receipt"]),
                                     other_claim["lease_token"])
            successor.settle(other_id, dict(other["settlement"]), other_claim["lease_token"])
            self.assertEqual(successor.get_receipt(other_id)["status"], "SETTLED")
        claimed = successor.claim(intent_of(record))
        successor.record_receipt(intent_id, receipt, claimed["lease_token"])
        successor.release(intent_id, claimed["lease_token"])
        self.assertEqual(successor.get_receipt(intent_id)["status"], "EFFECTED")
        self.assertIsNone(successor.get_settlement(intent_id),
                          "the successor ledger holds a settlement, so this is not the "
                          "post-receipt crash window")

        room = self.room / f"restart_{tag}"
        room.mkdir(exist_ok=True)
        # The SAME artifact base -- the same durable journal, the same capture logs -- with
        # this ledger and a checkpoint store of its own.  That is a successor Coordinator.
        for name in ("profile.json", "state.json"):
            shutil.copy(self.first.base / name, room / name)
        return self._rerun(room, ledger=successor_path)

    def _rerun(self, room: Path, *, ledger: Path) -> GraphRun:
        import contextlib
        previous = os.environ.get(GRAPH_CREDENTIAL_ENV)
        os.environ[GRAPH_CREDENTIAL_ENV] = GRAPH_CREDENTIAL_VALUE
        out, err = io.StringIO(), io.StringIO()
        escaped: BaseException | None = None
        code = -1
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = launcher.run_cli(
                    ["--adapter", "standalone",
                     "--state", str(room / "state.json"),
                     "--standalone-profile", str(room / "profile.json"),
                     "--artifact-base", str(self.first.artifact_base),
                     "--runtime-state", str(ledger),
                     "--checkpoint-store", str(room / "checkpoints.json"),
                     "--project-root", str(REPO), "--json"])
        except BaseException as exc:            # noqa: BLE001 - an ESCAPE is the finding
            escaped = exc
        finally:
            if previous is None:
                os.environ.pop(GRAPH_CREDENTIAL_ENV, None)
            else:
                os.environ[GRAPH_CREDENTIAL_ENV] = previous
        summary: dict = {}
        for line in reversed(out.getvalue().strip().splitlines()):
            try:
                summary = json.loads(line)
                break
            except ValueError:
                continue
        return GraphRun(exit_code=code, summary=summary, escaped=escaped, base=room,
                        artifact_base=self.first.artifact_base, run_id=self.first.run_id,
                        ledger_path=ledger, checkpoint_path=room / "checkpoints.json",
                        intents=self.first.intents_dir, argv_dump=self.first.argv_dump,
                        env_dump=self.first.env_dump, stdout=out.getvalue(),
                        stderr=err.getvalue())

    def test_the_first_run_really_settled_through_the_graph(self) -> None:
        """The premise, asserted -- an unsettled first run would make both cases vacuous."""
        self.assert_reached_a_terminal(self.first)
        self.assertEqual(self.first.summary.get("terminal_status"), "COMPLETED")
        row = self._first_worker_row()
        self.assertTrue(self._fence_of(row).strip(":"),
                        "the settlement row carries no session/incarnation fence at all")

    def test_a_matching_receipt_collects_the_effect_instead_of_re_running_it(self) -> None:
        """The POSITIVE half.  Same incarnation -> the successor harvests, never re-spawns.

        Without this half the negative one below could be satisfied by a fence that rejects
        everything, which would be a different bug wearing the same green tick.
        """
        row = self._first_worker_row()
        intent_id = row["intent_id"]
        before = self._spawns_for(self.first, intent_id)
        self.assertEqual(before, 1, "the first run did not spawn this dispatch exactly once")

        spawned_before = len(self.first.spawn_rows())
        restart = self._restart_with_receipt(intent_id=intent_id,
                                             external_id=self._fence_of(row), tag="match")
        self.assert_nothing_escaped(restart)
        after = len([r for r in journal_mod.ExecutionJournal(
            self.first.artifact_base, self.first.run_id).rows_for(intent_id)
            if r["kind"] == "SPAWN_OBSERVED"])
        self.assertEqual(after, before,
                         "the successor RE-RAN an effect that already existed; the whole "
                         "point of external_resume is that it never does")
        # Stronger than the per-intent count: the successor spawned NOTHING AT ALL.  With
        # the faithful crash window every other effect is already settled in its ledger,
        # so the whole run completes from collected evidence and no new process.
        self.assertEqual(len(restart.spawn_rows()), spawned_before,
                         "the successor spawned a process; a restart that holds every "
                         "settlement and the one receipt has nothing left to execute")
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        self.assertIsNotNone(
            FileRuntimeStateStore(restart.ledger_path).get_settlement(intent_id),
            "the successor ledger holds no settlement, so the existing effect was neither "
            "collected nor re-run and the dispatch is stranded")
        self.assertEqual(restart.summary.get("terminal_status"), "COMPLETED",
                         f"the successor did not carry the run to a terminal after "
                         f"collecting the existing effect: {restart.summary!r}")

    def test_a_replayed_settlement_from_a_foreign_incarnation_is_not_harvested(self) -> None:
        """The NEGATIVE half, and the one iteration 1 was missing.

        The receipt this successor holds names a DIFFERENT incarnation from the one that
        wrote the terminal row into the journal -- both of them real, both produced by real
        `execve`s in this same run.  That is precisely a replayed or foreign settlement, and
        it must NOT be collected as this dispatch's verdict.  The successor must refuse:
        `IDEMPOTENCY_RECOVERY_BLOCKED`, an observation, never a harvest and never a re-run.

        Mutation-sensitivity, verified by reproducing the reviewer's own mutation: insert
        `return stored` into `StandaloneAdapter.resume` immediately after the settlement
        lookup and this case fails, because the foreign settlement is harvested and the run
        reports COMPLETED.  Removing the fence from `StandaloneAdapter.settlement` fails it
        the same way, one rung earlier on the ladder.
        """
        row = self._first_worker_row()
        intent_id = row["intent_id"]
        others = {self._fence_of(other) for other in self.first.settlement_rows()
                  if other["intent_id"] != intent_id}
        self.assertTrue(others,
                        "this run produced only one incarnation, so there is no real "
                        "foreign fence to present and the case would be fabricated")
        foreign = sorted(others)[0]
        self.assertNotEqual(foreign, self._fence_of(row))
        before = self._spawns_for(self.first, intent_id)

        restart = self._restart_with_receipt(intent_id=intent_id, external_id=foreign,
                                             tag="foreign")
        self.assert_nothing_escaped(restart)
        after = len([r for r in journal_mod.ExecutionJournal(
            self.first.artifact_base, self.first.run_id).rows_for(intent_id)
            if r["kind"] == "SPAWN_OBSERVED"])
        self.assertEqual(after, before,
                         "the successor re-created an effect that already exists; a fence "
                         "mismatch is a refusal to HARVEST, never a licence to re-run")
        self.assertNotEqual(
            restart.summary.get("terminal_status"), "COMPLETED",
            "a settlement written by a FOREIGN incarnation was harvested as this "
            f"dispatch's verdict and the run reported success: {restart.summary!r}")
        self.assertEqual(
            (restart.summary.get("terminal_reason") or {}).get("code"),
            "IDEMPOTENCY_RECOVERY_BLOCKED",
            f"the refusal is not the fail-closed one the ladder owes: "
            f"{restart.summary!r}\n{restart.stderr}")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class E2EOwnershipRowAfterARealRunTests(_GraphAssertions, unittest.TestCase):
    """F5 / F9, over the artifacts a REAL `run_workflow.py` run left behind.

    **The residual, named rather than worked around.**  The graph's own PAUSE node is not
    reachable for `--adapter standalone` at this revision: `routing.pause_admissible`
    requires `human_approval`, `build_standalone_adapter` wires no approval port, and so no
    standalone run this CLI launches can pause.  That is ASSERTED below from the run's own
    frozen capability declaration rather than claimed in prose, and it is deliberately NOT
    "fixed" here -- wiring an approval port into the standalone composition would change what
    every standalone run declares, which is a change nobody asked for in this correction.

    What IS asserted end to end is everything short of that node: a real run through the
    real CLI leaves rows in the store the PAUSE and DISPOSE nodes read, and
    `executor._settlement_row` -- the ENGINE'S own function, the one those nodes call, not a
    helper of this runtime -- promotes them through `pause_policy` without a single axis the
    shared vocabulary refuses.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.room = Path(tempfile.mkdtemp(prefix="os37-e2e-row-"))
        cls.graph = execute_graph_cli(cls.room, run_id="run_e2erow")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.room, ignore_errors=True)

    def _adapter_over_the_run(self):
        """`build_standalone_adapter` over the FINISHED run's files.  A stranger process."""
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        spec = json.loads((self.room / "state.json").read_text(encoding="utf-8"))
        profile = json.loads((self.room / "profile.json").read_text(encoding="utf-8"))
        adapter, state = launcher.build_standalone_adapter(
            {"run_id": spec["run_id"], "thread_id": spec["thread_id"],
             "phases": spec["phases"]},
            artifact_base=self.graph.artifact_base, run_id=spec["run_id"],
            runtime_state=FileRuntimeStateStore(self.graph.ledger_path),
            profile_spec=profile)
        return adapter, state

    def test_every_row_a_real_run_left_is_discharged_by_the_pause_policy(self) -> None:
        """The finding's own consequence, over real rows: no `TERMINAL_OWNERSHIP_UNKNOWN`.

        Mutation-sensitivity: put `unknown` back into `SETTLEMENT_AXIS` (or drop
        `provenance_source` from `account_dispatch`) and these rows stop discharging.
        """
        from scripts.deterministic_workflow import executor
        self.assert_reached_a_terminal(self.graph)
        adapter, _state = self._adapter_over_the_run()
        dispatched = sorted({row["intent_id"] for row in self.graph.spawn_rows()})
        self.assertTrue(dispatched, "the run spawned nothing, so there are no rows to check")
        rows: dict[str, dict] = {}
        refused: list[str] = []
        for intent_id in dispatched:
            try:
                rows[intent_id] = executor._settlement_row(
                    adapter, adapter.pause_row_journal, intent_id,
                    now="1970-01-01T00:00:00Z")
            except Exception as exc:                # noqa: BLE001 - the REFUSAL is the point
                refused.append(f"{intent_id}: `executor._settlement_row` refused a row a "
                               f"REAL run produced -- {type(exc).__name__}: {exc}")
        self.assertEqual(refused, [], "\n".join(refused))
        for intent_id, row in rows.items():
            with self.subTest(intent=intent_id):
                self.assertEqual(row["settlement"], "settled",
                                 "a dispatch this run really settled is not reported settled")
                self.assertIn(row["terminal_disposition"],
                              pause_policy.AC1_DISCHARGING_DISPOSITIONS,
                              f"a settled standalone dispatch blocks the pause: {row!r}")
                self.assertNotEqual(row["terminal_disposition"], "DISPATCH_UNACCOUNTED")
                self.assertEqual(row["provenance_source"], "journal",
                                 "the row carries no provenance, so the pause authority "
                                 "cannot tell where this dispatch's terminal came from")
                self.assertEqual(row["terminal_origin"], "standalone_pty")
                self.assertTrue(row["terminal_title"],
                                "the row names no terminal at all")
                self.assertIn(row["terminal_role"],
                              ("WORKER", "PHASE_REVIEWER", "FINAL_REVIEWER"),
                              "the row does not name the role this dispatch ran as")

    def test_the_two_vocabularies_are_still_equal_over_a_real_runs_rows(self) -> None:
        """Every axis value a REAL run produced is one the pause authority admits."""
        self.assert_reached_a_terminal(self.graph)
        axes = {"settlement": pause_policy.SETTLEMENT_OUTCOMES,
                "worker_resource": pause_policy.WORKER_RESOURCE_OUTCOMES,
                "process_liveness": pause_policy.PROCESS_LIVENESS_STATES,
                "cleanup_authority": pause_policy.CLEANUP_AUTHORITY_STATES}
        observed = {name: set() for name in axes}
        for row in self.graph.journal_rows():
            for name in axes:
                value = (row.get("axes") or {}).get(name)
                if value:
                    observed[name].add(value)
        for name, admitted in axes.items():
            with self.subTest(axis=name):
                self.assertTrue(observed[name],
                                f"the run recorded no {name} value at all, so this "
                                "assertion would pass vacuously")
                self.assertEqual(sorted(observed[name] - set(admitted)), [],
                                 f"a real run emitted a {name} value `pause_policy` "
                                 "refuses, which is exactly finding #5")

    def test_the_unreachable_pause_node_is_measured_not_assumed(self) -> None:
        """The residual above, asserted from the run's own declaration.

        If a later change DOES wire an approval port into the standalone composition this
        fails, which is the correct outcome: the limitation stated in this class's docstring
        would then be stale and the pause node would need its own end-to-end case.
        """
        _adapter, state = self._adapter_over_the_run()
        declared = frozenset(state["adapter_capabilities"])
        self.assertIn(contracts.LIFECYCLE_SETTLEMENT, declared)
        missing = sorted(contracts.PAUSE_CAPABILITIES - declared)
        self.assertEqual(
            missing, ["human_approval"],
            "the standalone composition's pause capabilities changed; the graph PAUSE node "
            "may now be reachable and this correction's stated residual is out of date")
        self.assertFalse(
            routing.pause_admissible({"decision_state": "NEEDS_INPUT",
                                      "adapter_capabilities": sorted(declared)}),
            "`routing.pause_admissible` now admits a standalone run, so the PAUSE node is "
            "reachable and owes an end-to-end case of its own")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class E2EFailClosedPreflightTests(_GraphAssertions, unittest.TestCase):
    """F7's fail-closed prompt leg, at the graph rather than by calling preflight directly.

    The reviewer's objection to the iteration-1 F7 case was exact: the prompt and value
    checks called `standalone_preflight` itself, so they said nothing about whether a run an
    operator launches actually refuses.  Here the agent's declared `auth status` probe ASKS
    A QUESTION and never exits -- what a real CLI does when its session has lapsed -- and
    the whole run must refuse BEFORE any agent turn is spawned.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.room = Path(tempfile.mkdtemp(prefix="os37-e2e-prompt-"))
        cls.graph = execute_graph_cli(cls.room, run_id="run_e2eprompt",
                                      driver_env={"OS37_GA_AUTH_PROMPT": "1"})

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.room, ignore_errors=True)

    def test_an_interactive_credential_prompt_refuses_the_run_before_any_turn(self) -> None:
        """Ambiguity resolves to REFUSAL, and it refuses before the effect, not after."""
        self.assert_nothing_escaped(self.graph)
        self.assertNotEqual(self.graph.summary.get("terminal_status"), "COMPLETED",
                            "a CLI that asked for a login produced a COMPLETED workflow")
        self.assertNotEqual(self.graph.exit_code, 0)
        # Preflight's own bounded rehearsals DO spawn the binary -- that is what a
        # rehearsal is -- but no DISPATCH may be created.  The durable journal is the
        # authority on that: `SPAWN_OBSERVED` is written only for the real effect.
        self.assertEqual(
            self.graph.spawn_rows(), [],
            "an agent DISPATCH was created after the credential probe asked a question; "
            "the refusal must come before the effect, not after it")
        self.assertTrue(self.graph.probe_argv(),
                        "nothing was executed at all, so the refusal cannot be attributed "
                        "to the probe and this case would be vacuous")

    def test_the_refusal_names_the_prompt_rather_than_a_timeout(self) -> None:
        """A named refusal, so an operator can see WHY, not just that something failed."""
        self.assert_nothing_escaped(self.graph)
        settled = self.graph.settlement_rows()
        self.assertTrue(settled, "the refusal produced no settlement to read at all")
        reasons = set()
        for row in settled:
            verdict = (row["source_vocabulary"] or {}).get("completion_verdict") or {}
            reasons.add(str(verdict.get("reason") or ""))
            reasons.add(str(verdict.get("stage") or ""))
        self.assertTrue(
            {"auth_probe_interactive", "start_refused", "preflight"} & reasons,
            f"the refusal is not named as a preflight/credential refusal: {sorted(reasons)}")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class E2ERefusedInterruptOnARealSessionTests(_GraphAssertions, unittest.TestCase):
    """F11, on a REAL session, through the adapter's own port verb -- and the honest limit.

    The reviewer's objection was that the iteration-1 F11 cases construct a session
    directly, FABRICATE `session.record`, and call `session.interrupt`.  Two of those three
    are removed here: the session is created by `spawn_only` -- the adapter's own public
    verb -- against a real `execve`, so `record` is what the kernel gave it, and the verb
    under test is `StandaloneAdapter.interrupt`, the `ExecutionPort` method, on an adapter
    `build_standalone_adapter` composed.

    **The third is NOT removable and is not pretended away.**  `interrupt` has no caller in
    `graph.py`, `routing.py` or `executor.py` at this revision -- `OrcaAdapter.interrupt`
    returns `unsupported` for the same reason -- so there is no graph path that reaches it
    and no end-to-end run can be written that does.  That is asserted below from the
    engine's own source, so if a caller is ever added this case fails and asks for the
    end-to-end test that would then be possible.
    """

    def setUp(self) -> None:
        self.room = Path(tempfile.mkdtemp(prefix="os37-e2e-int-"))
        self.addCleanup(shutil.rmtree, self.room, True)

    def test_no_graph_or_routing_node_reaches_the_interrupt_verb(self) -> None:
        """The measured reason F11 has no end-to-end case, so the gap is visible not hidden."""
        import ast
        engine = REPO / "scripts" / "deterministic_workflow"
        callers: list[str] = []
        for name in ("graph.py", "routing.py", "executor.py"):
            tree = ast.parse((engine / name).read_text(encoding="utf-8"), filename=name)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "interrupt"):
                    callers.append(f"{name}:{node.lineno}")
        self.assertEqual(
            callers, [],
            "an engine node now calls `interrupt`, so finding #11 HAS a graph path and "
            "owes an end-to-end case rather than this structural one: "
            + ", ".join(callers))

    def test_a_refused_interrupt_through_the_adapter_port_moves_nothing(self) -> None:
        """The refusal itself, on a real child, via the composed adapter's port verb.

        Mutation-sensitivity: restore the unconditional journal write in
        `StandaloneSession.interrupt` and the axes assertion fails, because one unreadable
        process-table read would again replace a healthy dispatch's liveness with ignorance.
        """
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore

        worktree = self.room / "worktree"
        worktree.mkdir(parents=True, exist_ok=True)
        artifact_base = self.room / "artifact_base"
        artifact_base.mkdir(parents=True, exist_ok=True)
        profile = graph_profile_document(worktree=worktree, driver_env={})
        os.environ.setdefault(GRAPH_CREDENTIAL_ENV, GRAPH_CREDENTIAL_VALUE)
        ledger = FileRuntimeStateStore(self.room / "ledger.json")
        adapter, _state = launcher.build_standalone_adapter(
            {"run_id": "run_e2eint", "thread_id": "graph", "phases": ["DESIGN"]},
            artifact_base=artifact_base, run_id="run_e2eint", runtime_state=ledger,
            profile_spec=profile)
        intent = {**WORKER_INTENT_KEYS, "intent_id": "intent-e2eint",
                  "run_id": "run_e2eint", "role": "WORKER", "phase": "DESIGN"}
        claim = ledger.claim(intent)
        # `spawn_only` is the adapter's own non-blocking verb: a real `execve`, a real pty,
        # a real durable receipt, and NO settlement.  Nothing about the record is invented.
        adapter.spawn_only(intent, lease_token=claim["lease_token"], payload="work")
        session = adapter.runtime.session(intent["intent_id"])
        self.addCleanup(self._reap, session)
        self.assertIsNotNone(session.record, "spawn_only produced no process record")

        # Drive the real child to READY so the journal carries a HEALTHY, OBSERVED liveness
        # -- the thing the refusal used to overwrite.  Without this the case could pass
        # vacuously over an axis that was already ignorant.
        session.await_ready()
        before_axes = dict(session.journal.axes_for(session.intent_id))
        before_state, before_log = session.state, list(session.event_log)
        self.assertEqual(before_axes["process_liveness"], "live",
                         f"the live dispatch is not recorded live to begin with, so the "
                         f"assertion below would pass vacuously: {before_axes!r}")

        # The process table cannot be read THIS ONCE.  Ownership is refused.
        session._table_reader = lambda tty: {"tty": tty, "captured_at": 0.0, "rows": (),
                                             "readable": False}
        result = adapter.interrupt(intent["intent_id"], "stop")

        self.assertEqual(result["interrupt_outcome"], "not_owned",
                         "the ownership gate did not refuse, so this proves nothing")
        self.assertEqual(session.state, before_state, "a refusal moved the state")
        self.assertEqual(session.event_log, before_log,
                         "a refusal appended to the transition log")
        self.assertEqual(dict(session.journal.axes_for(session.intent_id)), before_axes,
                         "a refused interrupt overwrote a live dispatch's ownership axes "
                         "with ignorance, which is what fed finding #5's pause BLOCK")
        refusals = [row for row in session.journal.rows_for(session.intent_id)
                    if row["kind"] == "REFUSED"]
        self.assertTrue(refusals, "the refusal left no audit trail at all")
        self.assertEqual(refusals[-1]["event"], "",
                         "the audit record carries a lifecycle event again")
        self.assertEqual(refusals[-1]["source_vocabulary"]["refusal"],
                         "refusal_is_not_a_transition")

    def _reap(self, session) -> None:
        import signal
        try:
            session.pump(timeout_ms=50)
        except Exception:                                 # noqa: BLE001 - cleanup
            pass
        record = session.record
        if record:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(int(record["pgid"]), sig)
                except OSError:
                    break
            try:
                os.waitpid(int(record["pid"]), os.WNOHANG)
            except OSError:
                pass
        try:
            session.release()
        except Exception:                                 # noqa: BLE001 - cleanup
            pass
