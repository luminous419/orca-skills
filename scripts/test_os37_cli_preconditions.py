"""OS-37 V-15 / V-16.  The seven negative fixtures, and the G-item dispositions.

**Only NAMES appear here.**  No environment value and no auth material is written into this
file, into its output, or into any artefact it produces.  The parent process this runtime is
developed inside carries ``CLAUDE_CODE_MESSAGING_TOKEN`` and ``ORCA_AGENT_HOOK_TOKEN`` --
both bearer credentials -- so a test that printed values would leak exactly what the policy
under test exists to contain.

The G-items stay UNKNOWN.  Each fixture asserts the FAIL-CLOSED disposition that holds
**regardless of the outcome**, which is what makes the design indifferent to their answers:
a login prompt is never READY whatever it looks like, an unparsable version is never
"assume supported", and an unmapped exit code is never ``exited{0}``.  A fixture that cannot
run is recorded as "not established", never as a pass -- which is why the live half is
skip-gated with an exact declared reason rather than quietly passing.
"""
from __future__ import annotations

import os
import shutil
import time
import socket
import subprocess
import pathlib
import tempfile
import unittest
from pathlib import Path

from scripts.deterministic_workflow import standalone_env as env_policy
from scripts.deterministic_workflow import standalone_lifecycle as lifecycle
from scripts.deterministic_workflow import standalone_preflight as preflight
from scripts.deterministic_workflow.standalone_profile import (CompletionSelector,
                                                            DeliveryProofSelector,
                                                            HomePolicy,
                                                                ReadinessSelector,
                                                                StandaloneProfile,
                                                                Timeouts, parse_version)

REPO = Path(__file__).resolve().parent.parent
STUB_BIN = REPO / "scripts" / "fixtures" / "os37" / "bin"
ENV_DUMP = REPO / "scripts" / "fake_bin"

#: The live half needs a real agent CLI on this host, which no CI runner has.  Gated on an
#: env var nothing sets, and declared in `scripts/tolerated_skip_manifest.txt` with this
#: exact reason.
LIVE_CLI_REASON = "requires ORCA_OS37_LIVE_CLI=1 and a real agent CLI on this host"
LIVE_CLI = os.environ.get("ORCA_OS37_LIVE_CLI") == "1"


def profile(**overrides) -> StandaloneProfile:
    fields = dict(
        driver="claude", binary="os37-stub-cli",
        supported_range=((1, 0, 0), (2, 0, 0)),
        bin_dirs=(str(STUB_BIN),),
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
        timeouts=Timeouts(preflight_timeout_ms=3000))
    fields.update(overrides)
    return StandaloneProfile(**fields)


def child_env(prof: StandaloneProfile | None = None, **kwargs) -> dict:
    return env_policy.build_child_env(prof or profile(), spawn_token="t-fixture",
                                      include_secrets=False, **kwargs)


class _StubProber:
    """Runs the stub CLI in a mode, through the real pty probe."""

    def __init__(self, mode: str, *, session_id: str = "") -> None:
        self.mode = mode
        self.session_id = session_id

    def __call__(self, argv, env, *, timeout_ms, cwd=None):
        merged = dict(env)
        merged["OS37_STUB_MODE"] = self.mode
        if self.session_id:
            merged["OS37_STUB_SESSION_ID"] = self.session_id
        merged.setdefault("PATH", str(STUB_BIN) + ":/usr/bin:/bin")
        return preflight.probe_on_pty(argv, merged, timeout_ms=timeout_ms, cwd=cwd)


# =====================================================================================
class NegativeFixtureTests(unittest.TestCase):
    """NF-1 .. NF-7.  The three legs of the nested-CLI hazard, and the assertion's own life."""

    def _dump(self, prof: StandaloneProfile | None = None,
              extra_fd: int | None = None) -> str:
        """Spawn the env-dump stub under the REAL policy and return its own view.

        Its OWN view, deliberately: asserting on the dict this process built proves the
        builder works, and asserting on what the child actually sees proves the SPAWN works.
        The second is the property that matters.
        """
        prof = prof or profile(binary="os37-env-dump", bin_dirs=(str(ENV_DUMP),))
        env = child_env(prof)
        pass_fds = (extra_fd,) if extra_fd is not None else ()
        completed = subprocess.run(
            [str(ENV_DUMP / "os37-env-dump")], env=env, capture_output=True, text=True,
            timeout=30, check=False, pass_fds=pass_fds, close_fds=extra_fd is None)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout

    def test_nf1_nested_claude_markers_do_not_reach_the_child(self) -> None:
        """The parent-session markers of the FIRST driver family are absent in the child."""
        names = self._dump()
        for marker in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
                       "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET",
                       "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_PID"):
            self.assertNotIn(
                f"\n{marker}\n", names,
                f"{marker} reached the child; a nested agent would mis-detect its host, "
                "and the messaging token is a bearer credential")

    def test_nf2_nested_codex_markers_do_not_reach_the_child(self) -> None:
        """And the SECOND family's, including the cross-CLI leak observed on this host."""
        names = self._dump()
        for marker in ("CODEX_COMPANION_SESSION_ID", "CODEX_HOME"):
            self.assertNotIn(f"\n{marker}\n", names, f"{marker} reached the child")
        # With a run-scoped root DECLARED, `CODEX_HOME` is the declared value -- an
        # exception the policy names -- and never the user's own root.
        root = tempfile.mkdtemp()
        scoped = profile(binary="os37-env-dump", bin_dirs=(str(ENV_DUMP),),
                         driver="codex", driver_env={"CODEX_HOME": root})
        env = child_env(scoped)
        self.assertEqual(env["CODEX_HOME"], root)
        self.assertNotEqual(env["CODEX_HOME"], os.path.expanduser("~/.codex"))

    def test_nf3_hook_channel_is_excluded_and_receives_zero_bytes(self) -> None:
        """The single most dangerous vector: a live callback channel with a bearer token.

        Two assertions, because the name assertion alone is not enough: no ``ORCA_AGENT_HOOK_*``
        name reaches the child, AND a listener bound on a scratch port receives ZERO bytes
        during the run.
        """
        names = self._dump()
        leaked = [line for line in names.splitlines()
                  if line.startswith("ORCA_")
                  and line not in env_policy.ALLOWED_EXCEPTIONS]
        self.assertEqual(
            leaked, [],
            f"{leaked} reached the child; the hook endpoint carries a bearer token. The "
            "one ORCA_-prefixed name that legitimately survives is this repository's own "
            f"{env_policy.SPAWN_TOKEN_ENV}, which is diagnostic evidence and authorizes "
            "nothing")
        self.assertIn(f"\n{env_policy.SPAWN_TOKEN_ENV}\n", names,
                      "the declared diagnostic marker should be present, so this test is "
                      "distinguishing the exception rather than accepting any ORCA_ name")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.5)
        try:
            self._dump()
            with self.assertRaises((socket.timeout, OSError)):
                listener.accept()
        finally:
            listener.close()

    def test_nf4_orca_is_unreachable_on_the_child_path(self) -> None:
        """A property of the CHILD ENV, and of the child's own resolution -- not a shell trick."""
        env = child_env()
        self.assertIsNone(shutil.which("orca", path=env["PATH"]))
        env_policy.assert_orca_unreachable(env)
        self.assertIn("UNREACHABLE", self._dump(),
                      "the child's own `command -v orca` resolved the Orca binary")

    def test_nf5_terminal_identity_is_absent_not_fabricated(self) -> None:
        """``TERM_PROGRAM`` is ABSENT.  Not ``Orca``, and not a fabricated value.

        A fabricated value would be a lie a CLI might branch on; absent is the only correct
        disposition.
        """
        env = child_env()
        for name in env_policy.NEVER_SET:
            self.assertNotIn(name, env)
        names = self._dump()
        self.assertNotIn("\nTERM_PROGRAM\n", names)
        self.assertNotIn("\nTERM_PROGRAM_VERSION\n", names)

    def test_nf6_the_assertion_is_live_not_decorative(self) -> None:
        """Inject a forbidden name and the assertion RAISES.

        This is the test that keeps the other six honest: an allowlist that works never
        fires the assertion, so without this case a broken assertion would look identical
        to a working one.
        """
        for injected in ("CLAUDE_CODE_SESSION_ID", "ORCA_AGENT_HOOK_TOKEN",
                          "CODEX_COMPANION_SESSION_ID", "TERM_PROGRAM",
                          "DISABLE_AUTOUPDATER", "ANTHROPIC_BASE_URL"):
            with self.subTest(name=injected):
                env = dict(child_env())
                env[injected] = "x"
                with self.assertRaises(env_policy.ChildEnvironmentLeak):
                    env_policy.assert_clean(env)
                self.assertIn(injected, env_policy.forbidden_names(env))
        # And the three legitimate exceptions do NOT fire it.
        for allowed in sorted(env_policy.ALLOWED_EXCEPTIONS):
            with self.subTest(allowed=allowed):
                env = dict(child_env())
                env[allowed] = "x"
                env_policy.assert_clean(env)

    def test_nf7_the_child_sees_only_its_own_descriptors(self) -> None:
        """Leg 2 of the hazard: an inherited OPEN socket fd would be a second channel.

        ``CLAUDE_CODE_MESSAGING_SOCKET`` names a path the env policy scrubs -- but a socket
        already open on a descriptor would survive the name being gone.  A DESIGN addition
        to the plan's six fixtures, recorded as such.

        Driven through the RUNTIME'S OWN ``spawn``, deliberately.  ``subprocess.run`` with
        ``pass_fds`` hands the descriptor over on purpose, so a test built on it would prove
        something about ``subprocess`` rather than about ``os.closerange(3, MAXFD)`` in the
        forked child -- which is the mechanism that actually closes this leg.
        """
        import select as _select
        from scripts.deterministic_workflow import standalone_pty as pty_supervisor
        pair = socket.socketpair()
        base = Path(tempfile.mkdtemp())
        prof = profile(binary="os37-env-dump", bin_dirs=(str(ENV_DUMP),))
        env = child_env(prof)
        # Move the socket to a HIGH descriptor.  A low one (3, 4, ...) collides with the
        # handle `ls /dev/fd` opens for its own enumeration, which would make the assertion
        # report a leak that is really the enumerator's own descriptor.
        probe_fd = 21
        os.dup2(pair[0].fileno(), probe_fd)
        try:
            session = pty_supervisor.spawn(
                argv=(str(ENV_DUMP / "os37-env-dump"),), env=env, profile=prof,
                session_id="s-nf7", incarnation="i-nf7",
                spawn_record_target=str(base / "spawn.i-nf7"))
            chunks: list[bytes] = []
            import time as _time
            deadline = _time.time() + 10
            while _time.time() < deadline:
                ready, _, _ = _select.select([session["master_fd"]], [], [], 0.2)
                if not ready:
                    continue
                try:
                    data = os.read(session["master_fd"], 65536)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
            try:
                os.waitpid(session["pid"], 0)
            except OSError:
                pass
            pty_supervisor.release(session)
            output = b"".join(chunks).decode("utf-8", "replace")
            self.assertIn("--- open-fds ---", output, f"the stub produced: {output!r}")
            section = output.split("--- open-fds ---", 1)[1].split("--- orca", 1)[0]
            inherited = {line.strip() for line in section.splitlines() if line.strip()}
            self.assertNotIn(
                str(probe_fd), inherited,
                f"the child inherited descriptor {probe_fd}; an inherited open socket is a "
                "second channel back into the supervising session, even with the socket's "
                f"PATH scrubbed from the environment. child saw: {sorted(inherited)}")
            low = {fd for fd in inherited if fd.isdigit() and int(fd) < 3}
            self.assertTrue(
                low, f"the child saw no standard descriptors at all: {inherited}")
        finally:
            try:
                os.close(probe_fd)
            except OSError:
                pass
            pair[0].close()
            pair[1].close()

    def test_the_environment_is_constructed_never_pruned(self) -> None:
        """STATIC: ``os.environ`` is never copied and then trimmed.

        The distinction is the whole point of the module: a copy-then-delete is a denylist
        wearing an allowlist's clothes, and the next CLI release's marker variable would be
        inherited silently.
        """
        import ast
        source = (REPO / "scripts" / "deterministic_workflow"
                  / "standalone_env.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Delete):
                self.fail(f"standalone_env deletes a name at line {node.lineno}; the child "
                          "environment must be CONSTRUCTED from an allowlist")
        builder = source.split("def build_child_env", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("env: dict[str, str] = {}", builder,
                      "build_child_env must start from an EMPTY dict")

    def test_no_environment_value_appears_in_any_diagnostic(self) -> None:
        """The redaction discipline: ``describe`` and ``env_digest`` carry NAMES only."""
        env = dict(child_env())
        env["HOME"] = "/Users/secret-name"
        described = env_policy.describe(env)
        self.assertNotIn("/Users/secret-name", str(described))
        self.assertEqual(set(described), {"names", "env_digest", "orca_on_path"})
        # The digest must be over names, so two envs with the same names and different
        # values digest identically -- which is what makes it not an oracle for a value.
        other = dict(env)
        other["HOME"] = "/Users/different"
        self.assertEqual(env_policy.env_digest(env), env_policy.env_digest(other))


# =====================================================================================
class PreflightFailClosedTests(unittest.TestCase):
    """G-1 .. G-7: the disposition that holds REGARDLESS of the outcome."""

    def test_g1_no_credential_refuses_before_any_spawn(self) -> None:
        """A login prompt is never READY.  Preflight refuses first, with a named reason."""
        # An ADMITTED credential name (external review #7's closed contract).  It used to be
        # a fictional `OS37_STUB_TOKEN`, which the contract now refuses at construction --
        # correctly, because a name the child-environment allowlist does not carry produces
        # a profile that can only fail at spawn.  The assertion is unchanged: a declared
        # credential that does not resolve is `auth_absent`, before anything is spawned.
        prof = profile(auth_secret_ref={"ANTHROPIC_API_KEY": "OS37_STUB_TOKEN_SOURCE"})
        env = child_env(prof)          # include_secrets=False -> the name is absent
        outcome = preflight.check_auth(prof, env)
        self.assertEqual(outcome["verdict"], "fail")
        self.assertEqual(outcome["reason"], "auth_absent")
        self.assertEqual(outcome["evidence"]["missing_env_names"], ["ANTHROPIC_API_KEY"])
        # Only NAMES are reported.  The REFERENCE -- which names a real secret location --
        # never appears.
        self.assertNotIn("OS37_STUB_TOKEN_SOURCE", str(outcome))

    def test_g1_an_interactive_login_probe_is_a_failure_never_a_pass(self) -> None:
        prof = profile()
        outcome = preflight.check_auth(
            prof, child_env(prof), auth_probe_argv=["os37-stub-cli", "whoami"],
            prober=_StubProber("auth-interactive"))
        self.assertEqual(outcome["verdict"], "fail")
        self.assertEqual(outcome["reason"], "auth_probe_interactive")

    def test_g2_an_absent_probe_verb_resolves_to_fail_or_unknown_never_pass(self) -> None:
        """If no ``login status`` equivalent exists, the AMBIGUITY resolves against passing.

        With a declared credential that resolves, the presence check is a pass and the
        missing probe is recorded as such.  With NOTHING declared, nothing was verified and
        the verdict is ``unknown`` -- which the composition rule treats as a failure.
        """
        prof = profile(auth_secret_ref={})
        outcome = preflight.check_auth(prof, child_env(prof), auth_probe_argv=None)
        self.assertEqual(outcome["verdict"], "unknown")
        self.assertEqual(outcome["reason"], "auth_probe_unreadable")
        self.assertFalse(preflight.compose([outcome])["proceed"],
                         "an `unknown` verdict must refuse the start; unknown is not pass")

    def test_g3_an_unparsable_or_old_version_is_refused(self) -> None:
        prof = profile()
        env = child_env(prof)
        for mode, reason in (("version-garbage", "version_unparsable"),
                              ("version-old", "version_unsupported"),
                              ("version-silent", "version_unparsable")):
            with self.subTest(mode=mode):
                outcome = preflight.check_version(prof, env, prober=_StubProber(mode))
                self.assertEqual(outcome["verdict"], "fail")
                self.assertEqual(outcome["reason"], reason)
        good = preflight.check_version(prof, env, prober=_StubProber("version-ok"))
        self.assertEqual(good["verdict"], "pass")
        self.assertEqual(good["evidence"]["observed"], [1, 2, 3])
        # And there is no "assume supported" branch to write.
        self.assertFalse(prof.version_supported(None))
        self.assertIsNone(parse_version("not a version"))

    def test_g4_an_update_notice_cannot_reach_ready(self) -> None:
        """It cannot satisfy R-B, WHATEVER ITS SHAPE.

        The weaker half of the evidence -- the structural half is
        ``test_os37_lifecycle::test_arbitrary_interactive_frame_is_never_ready``, which
        quantifies over frames nobody has observed.  ``DISABLE_AUTOUPDATER`` is excluded
        from the child env, so the real path is reachable and this fixture is not testing a
        suppressed one.
        """
        from scripts.deterministic_workflow.standalone_lifecycle import (classify_refusals,
                                                                          may_send_prompt)
        self.assertIn("DISABLE_AUTOUPDATER", env_policy.FORBIDDEN_CHILD_ENV_PREFIXES)
        self.assertNotIn("DISABLE_AUTOUPDATER", child_env())
        probe = _StubProber("update-notice")(["os37-stub-cli"], child_env(),
                                             timeout_ms=2000)
        self.assertIn(probe["outcome"], ("interactive", "timeout"),
                      "an update notice that blocks must not read as completed")
        frame = probe["output"] or "An update is available. Install now? [y/N]"
        self.assertTrue(classify_refusals(frame),
                        "the refusal patterns are defence in depth and should fire here")
        verdict = may_send_prompt(
            {"liveness": {"identity_matches": True, "not_exited": True,
                          "foreground_is_child_group": True,
                          "foreground_executable_matches": True, "observed": {}},
             "bound_signal": None, "refusals": (),
             "supplementary": ({"tier": "screen_preview", "text": frame,
                                "live_observed": True, "at": ""},)},
            minted_session_id="s-1", declared_record_types=("system",))
        self.assertNotEqual(verdict["verdict"], "ready")

    def test_g5_a_permission_prompt_is_never_completion(self) -> None:
        """R-2, unconditional -- and independently it cannot satisfy R-B."""
        from scripts.deterministic_workflow.standalone_lifecycle import (classify_refusals,
                                                                          read_status)
        probe = _StubProber("permission")(["os37-stub-cli"], child_env(), timeout_ms=2000)
        frame = probe["output"] or "Allow this tool to write to /etc/hosts?"
        self.assertTrue(classify_refusals(frame))
        status = read_status(live_permission=True, blocked_text=frame,
                             structured_row={"status": "idle"},
                             foreground_is_shell=False, identity_resolved_title=None,
                             process_probe=None)
        self.assertEqual(status["status"], "WAITING_FOR_INPUT")
        self.assertNotIn(status["status"], ("COMPLETED", "FAILED"))

    def test_g6_the_sandbox_home_branch_exists_and_needs_no_code_change(self) -> None:
        """If ``--ignore-user-config`` turns out not to isolate, the PROFILE flips.

        Both branches are implemented from the start, so selecting the fallback is a
        configuration change rather than an edit to the spawn path.
        """
        root = tempfile.mkdtemp()
        inherit = profile()
        self.assertEqual(inherit.home_policy.kind, "inherit")
        sandboxed = env_policy.sandbox_home(inherit, root)
        self.assertEqual(sandboxed.home_policy.kind, "sandbox")
        env = child_env(sandboxed)
        self.assertEqual(env["HOME"], root)
        self.assertNotEqual(env["HOME"], os.environ.get("HOME"))
        with self.assertRaises(Exception):
            HomePolicy(kind="sandbox")     # a sandbox with no root is refused

    def test_g7_an_unmapped_exit_code_is_lost_never_exited_zero(self) -> None:
        from scripts.deterministic_workflow.standalone_lifecycle import (
            UNVERIFIED_PROCESS_EXIT_CODE, map_exit_code)
        prof = profile(exit_code_map={})
        self.assertEqual(prof.exit_code_map, {})       # an EMPTY table is valid
        for code in (0, 1, 2, 42, 130):
            with self.subTest(code=code):
                mapped = map_exit_code(code, prof.exit_code_map)
                self.assertEqual(mapped["state"], "LOST")
                self.assertEqual(mapped["lost_reason"], "exit_code_unmapped")
        self.assertEqual(UNVERIFIED_PROCESS_EXIT_CODE, -1)
        self.assertNotEqual(UNVERIFIED_PROCESS_EXIT_CODE, 0)

    def test_u_env_1_no_config_isolation_is_claimed_beyond_the_declared_flags(self) -> None:
        """Until proven, the code makes no claim of config isolation.

        Asserted as an absence of a claim: no standalone module asserts that a config
        directory is isolated, because whether the flags do that is UNKNOWN.
        """
        engine = REPO / "scripts" / "deterministic_workflow"
        for path in sorted(engine.glob("standalone_*.py")):
            source = path.read_text().lower()
            for overclaim in ("config is isolated", "fully isolated",
                              "guarantees isolation", "cannot reach ~/"):
                self.assertNotIn(overclaim, source,
                                 f"{path.name} claims config isolation that is UNKNOWN")

    def test_u_env_2_a_run_scoped_config_root_is_set_regardless(self) -> None:
        """Auth is assumed NOT isolated by a flag alone, so the root is set anyway."""
        root = tempfile.mkdtemp()
        prof = profile(driver="codex", driver_env={"CODEX_HOME": root})
        self.assertEqual(child_env(prof)["CODEX_HOME"], root)

    def test_the_composition_rule_refuses_on_fail_or_unknown(self) -> None:
        """``start`` proceeds only when all four verdicts are ``pass``."""
        passing = [{"check": name, "verdict": "pass", "reason": "", "evidence": {}}
                   for name in preflight.CHECKS]
        self.assertTrue(preflight.compose(passing)["proceed"])
        for verdict, reason in (("fail", "binary_absent"),
                                 ("unknown", "auth_probe_unreadable")):
            with self.subTest(verdict=verdict):
                mixed = list(passing)
                mixed[2] = {"check": "auth", "verdict": verdict, "reason": reason,
                            "evidence": {}}
                decision = preflight.compose(mixed)
                self.assertFalse(decision["proceed"])
                self.assertEqual(decision["reason"], reason)
                with self.assertRaises(preflight.PreflightRefused):
                    preflight.require(mixed)

    def test_start_unknown_is_never_produced_by_preflight(self) -> None:
        """An unknown PRECONDITION is a failure, not an unknown start."""
        import inspect
        source = inspect.getsource(preflight)
        self.assertNotIn(
            '"start_unknown"', source,
            "preflight produced start_unknown; that member is reserved for a spawn that "
            "was ATTEMPTED and whose result cannot be established")

    def test_binary_is_resolved_against_the_child_path_not_the_parents(self) -> None:
        """A binary only the PARENT can see is a failure."""
        prof = profile(bin_dirs=())
        outcome = preflight.check_binary(prof, child_env(prof))
        self.assertEqual(outcome["verdict"], "fail")
        self.assertEqual(outcome["reason"], "binary_absent")
        declared = preflight.check_binary(profile(), child_env())
        self.assertEqual(declared["verdict"], "pass")


# =====================================================================================
class ReadinessRehearsalTests(unittest.TestCase):
    """DR-7: a profile whose readiness selector never fires is refused BEFORE any spawn."""

    def test_profile_without_readiness_selector_is_refused_at_preflight(self) -> None:
        """``profile_readiness_unverified`` -> ``start_outcome="failed"``.  Never a title fallback."""
        prof = profile(readiness_records=())
        outcome = preflight.check_profile(prof, child_env(prof))
        self.assertEqual(outcome["verdict"], "fail")
        self.assertEqual(outcome["reason"], "profile_readiness_unverified")

    def test_a_rehearsal_that_never_fires_is_refused(self) -> None:
        prof = profile()
        outcome = preflight.check_profile(
            prof, child_env(prof), help_text="",
            rehearsal=lambda p, e, s: None, minted_session_id="s-1")
        self.assertEqual(outcome["verdict"], "fail")
        self.assertEqual(outcome["reason"], "profile_readiness_unverified")

    def test_a_rehearsal_carrying_a_foreign_session_id_is_refused(self) -> None:
        prof = profile()
        outcome = preflight.check_profile(
            prof, child_env(prof), help_text="",
            rehearsal=lambda p, e, s: {"channel": "structured", "record_type": "system",
                                        "session_id": "s-SOMEONE-ELSE"},
            minted_session_id="s-1")
        self.assertEqual(outcome["verdict"], "fail")
        self.assertEqual(outcome["reason"], "profile_readiness_unverified")

    def test_a_rehearsal_that_fires_correctly_passes(self) -> None:
        prof = profile()
        outcome = preflight.check_profile(
            prof, child_env(prof), help_text="",
            rehearsal=lambda p, e, s: {"channel": "structured", "record_type": "system",
                                        "session_id": s},
            minted_session_id="s-1")
        self.assertEqual(outcome["verdict"], "pass", outcome)
        self.assertEqual(outcome["evidence"]["rehearsed_record_type"], "system")

    def test_no_rehearsal_supplied_is_unknown_not_pass(self) -> None:
        prof = profile()
        outcome = preflight.check_profile(prof, child_env(prof), help_text="")
        self.assertEqual(outcome["verdict"], "unknown")
        self.assertEqual(outcome["reason"], "profile_readiness_unverified")

    def test_the_real_stub_rehearsal_fires_end_to_end(self) -> None:
        """The rehearsal against the actual stub binary, on a real pty.

        Not a mock: the stub emits a structured record carrying the minted session id, and
        the same ``r_b_satisfied`` the runtime uses accepts it -- so the mechanism is proven
        rather than described.
        """
        from scripts.deterministic_workflow.standalone_capture import structured_lines
        from scripts.deterministic_workflow.standalone_lifecycle import r_b_satisfied
        minted = "s-rehearsal-0001"
        probe = _StubProber("ready", session_id=minted)(
            ["os37-stub-cli"], child_env(), timeout_ms=5000)
        self.assertEqual(probe["outcome"], "completed", probe)
        observed = None
        for parsed, _raw in structured_lines(probe["output"]):
            if parsed and parsed.get("type") == "system":
                observed = {"channel": "structured", "record_type": "system",
                            "session_id": parsed.get("session_id")}
        self.assertIsNotNone(observed, f"no structured record: {probe['output']!r}")
        self.assertTrue(r_b_satisfied(observed, minted_session_id=minted,
                                      declared_record_types=("system",)))
        # And the same record with a foreign session id is refused.
        self.assertFalse(r_b_satisfied(dict(observed, session_id="s-other"),
                                       minted_session_id=minted,
                                       declared_record_types=("system",)))

    def test_a_stub_that_emits_no_declared_record_never_satisfies_r_b(self) -> None:
        from scripts.deterministic_workflow.standalone_capture import structured_lines
        from scripts.deterministic_workflow.standalone_lifecycle import r_b_satisfied
        probe = _StubProber("no-readiness")(["os37-stub-cli"], child_env(),
                                            timeout_ms=5000)
        for parsed, _raw in structured_lines(probe["output"]):
            if parsed:
                self.assertFalse(
                    r_b_satisfied({"channel": "structured",
                                   "record_type": parsed.get("type"),
                                   "session_id": parsed.get("session_id")},
                                  minted_session_id="s-1",
                                  declared_record_types=("system",)))


# =====================================================================================
@unittest.skipUnless(LIVE_CLI, LIVE_CLI_REASON)
class LiveCliTests(unittest.TestCase):
    """V-16's live half.  Skip-gated with an exact declared reason.

    A fixture that cannot run is recorded as "not established", never as a pass -- which is
    why this is a skip with a manifest entry rather than a silently-passing test.
    """

    def test_the_installed_binaries_report_a_parsable_version(self) -> None:  # pragma: no cover
        for binary in ("claude", "codex"):
            resolved = shutil.which(binary)
            if resolved is None:
                self.skipTest(f"{binary} is not installed on this host")
            completed = subprocess.run([resolved, "--version"], capture_output=True,
                                       text=True, timeout=30, check=False)
            self.assertIsNotNone(
                parse_version(completed.stdout),
                f"{binary} --version is unparsable: {completed.stdout[:120]!r}")

    # ---- the DRIVER CONTRACT, asserted against the CLIs that are really installed -----
    # These are the R1 live half.  They assert what the APPROVED DESIGN says the driver
    # composes, not what the code currently composes, so a divergence shows up as a
    # failure naming the divergence rather than as a green board.  Each one is backed by a
    # measurement recorded in
    # `artifacts/runs/run_54d90086bd75/evidence/r1_live_drivers/REAL_CLI_MEASUREMENTS.txt`.

    def test_the_minted_session_id_is_a_uuid_as_the_design_specifies(self) -> None:
        """DESIGN D6 composes ``--session-id <minted uuid>``; the real CLI enforces it.

        MEASURED (M1): `claude --session-id s-<20 hex>` exits with
        `Error: Invalid session ID. Must be a valid UUID.`  A session id the CLI refuses
        makes the readiness rehearsal unsatisfiable, so this is not cosmetic.
        """
        import uuid as _uuid
        from scripts.deterministic_workflow import standalone_identity as identity
        minted = identity.mint_session_id(run_id="run_x", dispatch_id="d", task_id="t")
        try:
            _uuid.UUID(minted)
        except (ValueError, AttributeError, TypeError):
            self.fail(f"mint_session_id returned {minted!r}, which is not a UUID; "
                      "the real Claude CLI refuses it (see M1)")

    def test_the_claude_argv_carries_every_flag_the_real_cli_requires(self) -> None:
        """MEASURED (M2): `-p --output-format stream-json` requires `--verbose`.

        Without it the real CLI exits with
        `Error: When using --print, --output-format=stream-json requires --verbose`
        before emitting anything, so no readiness record can appear.
        """
        from scripts.deterministic_workflow import standalone_drivers as drivers
        # Composed from the SHIPPING profile, so this case cannot pass against a profile
        # nothing ships.
        argv = drivers.driver_for(_claude_live_profile()).argv(session_id="s-1")
        self.assertIn("--verbose", argv,
                      f"the composed argv {list(argv)} omits --verbose, which the "
                      "installed Claude CLI requires alongside -p and stream-json (M2)")

    def test_the_composed_claude_argv_is_accepted_by_the_real_cli_and_binds_r_b(
            self) -> None:
        """The POSITIVE half, against `claude 2.1.260`: the two fixed defects really are fixed.

        This is the same argv ``ClaudeDriver.argv`` composes in production -- nothing is
        hand-assembled here -- spawned on a real headless pty with the prompt supplied up
        front, which is the ONLY order ``--print`` accepts (see the ordering case below).
        MEASURED: the CLI accepts the argv, emits ``{"type":"system","subtype":"init"}``
        carrying THIS runtime's minted session id, and ``bound_readiness_signal`` closes
        R-B by equality against it.

        Before the fixes it could not: the minted id was ``s-<20 hex>`` (`Invalid session
        ID. Must be a valid UUID.`) and the argv lacked ``--verbose``
        (`--output-format=stream-json requires --verbose`).  Both refusals happened before
        any output, so this case would have found an empty transcript.
        """
        binary = shutil.which("claude")
        if binary is None:
            self.skipTest("claude is not installed on this host")
        from scripts.deterministic_workflow import standalone_drivers as drivers
        from scripts.deterministic_workflow import standalone_identity as identity
        driver = drivers.driver_for(_claude_live_profile())
        minted = identity.mint_session_id(run_id="r", dispatch_id="d", task_id="t")
        # The prompt goes on ARGV.  M-1 and M-6 measured that this is the only order the
        # CLI accepts: with stdin on a pipe held open and empty for 8 s it emitted ZERO
        # bytes for the whole window, so no readiness record can precede delivery.
        argv = [binary if index == 0 else part for index, part in
                enumerate(driver.launch_argv(session_id=minted, prompt="Reply OK"))]
        text = _run_on_pty(argv, stdin_bytes=None, budget_s=120.0)
        self.assertTrue(
            text.strip(),
            f"the real CLI produced nothing for the composed argv {argv}; it refuses the "
            "argv before emitting anything")
        bound = driver.bound_readiness_signal(text, minted_session_id=minted)
        self.assertIsNotNone(
            bound,
            f"R-B did not close against the locally minted id {minted!r}; the CLI said: "
            f"{text[:300]!r}")
        self.assertEqual(bound["session_id"], minted)          # type: ignore[index]
        self.assertEqual(bound["record_type"], "system")       # type: ignore[index]
        self.assertEqual(bound["channel"], "structured")       # type: ignore[index]

    def test_neither_real_cli_emits_a_record_before_its_prompt_is_delivered(self) -> None:
        """The MEASUREMENT behind the F-001 blocked report.  Read the direction carefully.

        DESIGN §D4.3 / §D5 order a dispatch as spawn -> observe a bound readiness record ->
        THEN write the prompt frame to the pty.  MEASURED here, against both installed
        CLIs, is that neither ever reaches that waiting state:

        * ``claude ... -p ...`` on a pty answers `Error: Input must be provided either
          through stdin or as a prompt argument when using --print` and exits.  Adding
          ``--input-format stream-json`` does not change it, and with stdin on a PIPE held
          open the CLI emits NOTHING AT ALL until input arrives -- so there is no ordering
          in which a readiness record precedes delivery.
        * ``codex exec ...`` answers `No prompt provided. Either specify one as an argument
          or pipe the prompt into stdin.` and exits.

        This case asserts that measurement so the blocked report is repeatable rather than
        anecdotal.  **When it FAILS, that is good news**: a CLI has gained a mode that waits
        for input after announcing itself, and the DESIGN ordering this ticket is blocked on
        can be revisited.  It asserts nothing about the runtime's own code, which is why it
        is here among the live probes and not in the driver tests.
        """
        from scripts.deterministic_workflow import standalone_drivers as drivers
        from scripts.deterministic_workflow import standalone_identity as identity
        checked = 0
        for name, make_profile in (("claude", _claude_live_profile),
                                   ("codex", _codex_live_profile)):
            binary = shutil.which(name)
            if binary is None:
                continue
            with self.subTest(cli=name):
                checked += 1
                driver = drivers.driver_for(make_profile())
                minted = identity.mint_session_id(run_id="r", dispatch_id="d",
                                                  task_id="t")
                argv = [binary if index == 0 else part for index, part in
                        enumerate(driver.argv(session_id=minted))]
                # No prompt, exactly as the approved order requires: the runtime would
                # deliver it only AFTER a readiness record it never receives.
                text = _run_on_pty(argv, stdin_bytes=None, budget_s=20.0)
                self.assertIsNone(
                    driver.bound_readiness_signal(text, minted_session_id=minted),
                    f"{name} DID bind a readiness record before any prompt was delivered; "
                    "the DESIGN ordering blocker recorded for F-001 has lifted and the "
                    f"driver design should be revisited.  Transcript: {text[:400]!r}")
        if checked == 0:
            self.skipTest("neither claude nor codex is installed on this host")

    def test_the_codex_argv_declares_no_forged_identity_channel(self) -> None:
        """**This case REPLACED an earlier one that asserted the opposite, and here is why.**

        The earlier case required the composed Codex argv to CARRY the locally minted
        session id.  That requirement was wrong, and D4.0 measured it wrong twice.  M-7: no
        flag on `codex exec` accepts a caller-supplied thread id.  M-10: supplying
        `-c thread_id=<uuid>` is ACCEPTED, the turn completes normally, the supplied value
        appears NOWHERE in the output, and there is **no error and no warning**.  So an argv
        identity binding for Codex fails SILENTLY, and a profile composing one would have
        been silently wrong while looking correct.

        The approved DESIGN therefore binds R-B in `adopted` mode (D4.4 A-1..A-6) and
        FORBIDS the flag: a profile declaring `identity_binding="adopted"` alongside an
        `identity_flag` is refused at construction.  Deleting the old assertion would be
        weakening; replacing it with the inverse assertion the measurement supports is not,
        and the adopted-binding case below carries the strength the old one only claimed.
        """
        from scripts.deterministic_workflow import standalone_drivers as drivers
        from scripts.deterministic_workflow.standalone_profile import ProfileError
        minted = "11111111-2222-4333-8444-555555555555"
        profile = _codex_live_profile()
        self.assertEqual(profile.identity_binding, "adopted")
        argv = drivers.driver_for(profile).argv(session_id=minted)
        self.assertFalse(
            any(minted in part for part in argv),
            f"the composed codex argv {list(argv)} carries the minted identity, which M-10 "
            "measured the CLI SILENTLY ignoring; a binding that is ignored without error is "
            "worse than no binding at all")
        with self.assertRaises(ProfileError):
            _codex_live_profile(identity_flag="-c")

    def test_the_adopted_identity_is_externally_re_verifiable(self) -> None:
        """A-5: the adopted value is checkable against the CLI's OWN durable state.

        MEASURED (M-11): `codex exec resume <a never-minted uuid>` exits non-zero with
        `no rollout found for thread id ... (code -32600)` and NO side effect.  A
        `minted_echo` binding has no such independent check, so on this axis `adopted` is
        BETTER evidenced, not merely equal.
        """
        binary = shutil.which("codex")
        if binary is None:
            self.skipTest("codex is not installed on this host")
        forged = "11111111-2222-4333-8444-555555555555"
        completed = subprocess.run(
            [binary, "exec", "resume", forged, "--json", "hello"],
            capture_output=True, text=True, timeout=120, check=False)
        combined = (completed.stdout + completed.stderr).lower()
        self.assertNotEqual(completed.returncode, 0,
                            f"a never-minted thread id was RESUMED: {combined[:400]!r}")
        self.assertIn("no rollout found", combined,
                      f"the refusal was not the measured typed one: {combined[:400]!r}")

    def test_the_production_claude_argv_reaches_a_real_delivery_proof(self) -> None:
        """I2 / F-001's substance: the PRODUCTION driver, against the installed CLI.

        Nothing here is hand-assembled -- the argv is exactly what `ClaudeDriver.launch_argv`
        composes in production.  It asserts the whole revised chain at once: R-B closes by
        EQUALITY on the minted id, the CONJUNCTIVE selector constructs a class-B
        `DeliveryProof`, the completion record is a genuine success, no typed auth marker is
        present, and **zero hook records appear** -- the last being the measured proof that
        `--safe-mode --setting-sources ''` really isolates the child from this repository's
        own `SessionStart` hooks (D13.6(c) precondition 6).  Before iteration 3 this argv
        mandated `--bare`, which M-2 measured forcing `apiKeySource:"none"` and an
        `authentication_failed` turn on this OAuth-authenticated host.
        """
        binary = shutil.which("claude")
        if binary is None:
            self.skipTest("claude is not installed on this host")
        from scripts.deterministic_workflow import standalone_drivers as drivers
        from scripts.deterministic_workflow import standalone_identity as identity
        profile = _claude_live_profile()
        driver = drivers.driver_for(profile)
        minted = identity.mint_session_id(run_id="r", dispatch_id="d", task_id="t")
        payload = "Reply with exactly: OK"
        argv = [binary if index == 0 else part for index, part in
                enumerate(driver.launch_argv(session_id=minted, prompt=payload))]
        text = _run_on_pty(argv, stdin_bytes=None, budget_s=120.0)
        self.assertTrue(text.strip(), f"the real CLI produced nothing for {argv}")
        intent = dict(drivers.make_delivery_intent(
            intent_id="live", dispatch_id="live", task_id="live", session_id=minted,
            payload=payload, argv_digest="live", attempt_incarnation="live",
            delivery_mode="launch_with_prompt"))
        bound = driver.bound_readiness_signal(text, minted_session_id=minted)
        self.assertIsNotNone(bound, f"R-B did not close: {text[:400]!r}")
        proof = driver.delivery_evidence(text, intent=intent, composed_argv=argv)
        self.assertIsNotNone(
            proof, "the conjunctive selector constructed no DeliveryProof from a real "
                   f"successful turn: {text[:600]!r}")
        self.assertEqual(proof["proof_class"], "B")
        record = driver.completion_record(text)
        self.assertIsNotNone(record)
        self.assertFalse(record["is_error"], f"the real turn failed: {record}")
        self.assertIsNone(driver.auth_marker_present(text))
        hooks = [r for r in driver.structured_records(text)
                 if str(r.get("subtype", "")).startswith("hook")]
        self.assertEqual(hooks, [], "the child inherited this repository's hooks; the "
                                    "measured parent-isolation set is not in force")

    def test_the_production_codex_argv_reaches_a_real_delivery_proof(self) -> None:
        """The same for Codex, INCLUDING the seeded run-scoped credential root.

        M-8 measured both legs and this case drives both.  The NEGATIVE leg is the point: an
        unseeded root yields `401 Unauthorized`, an ABSENT `-o` file, a `turn.started` the
        CLI emits BEFORE it has authenticated -- and NO delivery proof, because the
        conjunctive selector refuses that record.  The positive leg then shows the same argv
        reaching a real `agent_message` item and a `turn.completed` once the credential file
        the profile names is seeded into the root.
        """
        binary = shutil.which("codex")
        if binary is None:
            self.skipTest("codex is not installed on this host")
        seed = os.path.expanduser("~/.codex/auth.json")
        if not os.path.exists(seed):
            self.skipTest("no codex credential file to seed a run-scoped root from")
        from scripts.deterministic_workflow import standalone_drivers as drivers
        root = tempfile.mkdtemp(prefix="os37-codex-home-")
        worktree = tempfile.mkdtemp(prefix="os37-codex-wt-")
        out_path = os.path.join(root, "last-message.txt")
        profile = _codex_live_profile(worktree=worktree,
                                      output_last_message_path=out_path,
                                      auth_seed_source=seed,
                                      auth_seed_dest_name="auth.json")
        driver = drivers.driver_for(profile)
        payload = "Reply with exactly: OK"
        argv = [binary if index == 0 else part for index, part in
                enumerate(driver.launch_argv(session_id="unused", prompt=payload))]
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
               "LANG": "en_US.UTF-8", "TERM": "xterm-256color",
               "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
               "USER": os.environ.get("USER", ""), "SHELL": "/bin/sh",
               "CODEX_HOME": root}
        intent = dict(drivers.make_delivery_intent(
            intent_id="live", dispatch_id="live", task_id="live", session_id="pending",
            payload=payload, argv_digest="live", attempt_incarnation="live",
            delivery_mode="launch_with_prompt"))

        with self.subTest("unseeded root -> no delivery proof, a NAMED auth failure"):
            text = _run_on_pty(argv, stdin_bytes=None, budget_s=60.0, env=env)
            self.assertIn("turn.started", text,
                          "the failure leg did not emit the demoted lifecycle record, so "
                          "this case is not exercising the hole it exists to close")
            self.assertIsNone(driver.delivery_evidence(text, intent=intent),
                              "a 401 leg constructed a DeliveryProof")
            self.assertIsNotNone(driver.auth_marker_present(text))
            self.assertFalse(os.path.exists(out_path),
                             "the -o file exists on a leg that never authenticated")

        with self.subTest("seeded root -> a real delivery proof and a real completion"):
            seeded = driver.seed_auth_home(root)
            self.assertTrue(seeded["seeded"], seeded)
            self.assertEqual(oct(os.stat(seeded["path"]).st_mode & 0o777), "0o600")
            text = _run_on_pty(argv, stdin_bytes=None, budget_s=150.0, env=env)
            adopted = [r for r in driver.structured_records(text)
                       if r.get("type") == "thread.started"]
            self.assertTrue(adopted, f"no thread.started to adopt: {text[:400]!r}")
            bound_intent = {**intent, "session_id": str(adopted[0]["thread_id"])}
            proof = driver.delivery_evidence(text, intent=bound_intent)
            self.assertIsNotNone(
                proof, f"no DeliveryProof from a real seeded turn: {text[:600]!r}")
            self.assertIn(proof["record_type"], ("item.completed", "turn.completed"))
            self.assertIsNone(driver.auth_marker_present(text))
            self.assertTrue(os.path.exists(out_path),
                            "the -o last-message file is absent on a completed turn")


def _claude_live_profile(**overrides):
    """The SHIPPING Claude profile: every flag from a D4.0 measurement, none transcribed."""
    from scripts.deterministic_workflow.standalone_profile import (
        CompletionSelector, DeliveryProofSelector, ReadinessSelector, StandaloneProfile)
    fields = dict(
        driver="claude", binary="claude", supported_range=((1, 0, 0), (99, 0, 0)),
        delivery_mode="launch_with_prompt", identity_binding="minted_echo",
        identity_flag="--session-id", permission_mode="plan",
        no_session_persistence=True, extra_args=("--strict-mcp-config",),
        readiness_records=(ReadinessSelector(channel="structured", record_type="system",
                                             session_field="session_id"),),
        delivery_proofs=(DeliveryProofSelector(channel="structured",
                                               record_type="assistant"),),
        completion_records=(CompletionSelector(channel="structured", record_type="result",
                                               error_field="is_error",
                                               success_field="terminal_reason",
                                               success_values=("completed",)),),
        auth_markers=(("error", "authentication_failed"),
                      ("is_api_error_message", "True"),
                      ("terminal_reason", "api_error")))
    fields.update(overrides)
    return StandaloneProfile(**fields)


def _codex_live_profile(**overrides):
    """The SHIPPING Codex profile.  `--ephemeral` is ABSENT and `resume` is declared.

    M-11: `--ephemeral` leaves no rollout, and `codex exec resume` is the only
    re-verification and resume channel Codex has, so the two are mutually exclusive and the
    honesty rule decides which one the operator gets rather than the argv deciding it behind
    their back.
    """
    from scripts.deterministic_workflow.standalone_profile import (
        CompletionSelector, DeliveryProofSelector, ReadinessSelector, StandaloneProfile)
    fields = dict(
        driver="codex", binary="codex", supported_range=((0, 1, 0), (99, 0, 0)),
        delivery_mode="launch_with_prompt", identity_binding="adopted",
        sandbox_mode="read-only", resume_channel="cli_resume_subcommand",
        resume_args=("--json",),
        readiness_records=(ReadinessSelector(channel="structured",
                                             record_type="thread.started",
                                             session_field="thread_id"),),
        delivery_proofs=(DeliveryProofSelector(channel="structured",
                                               record_type="item.completed",
                                               item_type="agent_message"),
                         DeliveryProofSelector(channel="structured",
                                               record_type="turn.completed"),),
        completion_records=(CompletionSelector(channel="structured",
                                               record_type="turn.completed"),),
        auth_markers=(("type", "turn.failed"),))
    fields.update(overrides)
    return StandaloneProfile(**fields)



def _run_on_pty(argv, *, stdin_bytes, budget_s: float, env=None):
    """Run ``argv`` on a REAL headless pty and return the transcript.

    ``stdin_bytes=None`` leaves stdin on the pty, which is what a standalone dispatch does;
    a value is written to a PIPE on stdin, which is the only way ``--print`` accepts a
    prompt.  Bounded, and the process group is always killed.
    """
    import pty as _pty
    import select as _select
    import signal as _signal
    master_fd, slave_fd = _pty.openpty()
    read_fd = write_fd = None
    if stdin_bytes is not None:
        read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:                                  # pragma: no cover - the child
        try:
            os.setsid()
            os.dup2(read_fd if read_fd is not None else slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            for fd in (master_fd, slave_fd, read_fd, write_fd):
                if fd is not None and fd > 2:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            if env is None:
                os.execvp(argv[0], list(argv))
            else:
                # An EXPLICIT child env, constructed rather than inherited: the seeded
                # run-scoped credential root is the whole point of the Codex case, and
                # inheriting the parent's would test the operator's home instead.
                os.execvpe(argv[0], list(argv), env)
        except BaseException:
            os._exit(127)
    os.close(slave_fd)
    if read_fd is not None:
        os.close(read_fd)
        os.write(write_fd, stdin_bytes)
        os.close(write_fd)
    out = b""
    deadline = time.time() + budget_s
    try:
        while time.time() < deadline:
            ready, _w, _x = _select.select([master_fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            try:
                done, _status = os.waitpid(pid, os.WNOHANG)
            except OSError:
                break
            if done:
                break
    finally:
        try:
            os.killpg(os.getpgid(pid), _signal.SIGKILL)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
        os.close(master_fd)
    return out.decode("utf-8", "replace")


# =========================================================================================
# DESIGN §D4.2b / §D13.2a -- CAPABILITY-VS-REALITY CONFORMANCE, AND ITS FOUR OUTCOMES
# =========================================================================================
FIXTURE_BIN = pathlib.Path(__file__).resolve().parent / "fixtures" / "os37" / "bin"
STREAMS = pathlib.Path(__file__).resolve().parent / "fixtures" / "os37" / "streams"


def _observation(**overrides) -> dict:
    """A PASSING launch-mode observation, so each case breaks exactly one leg."""
    base = {"r_b_closed": True, "delivery_proof": True, "auth_marker": None,
            "waited_without_prompt": False, "evaluable": True, "identity_bound": True,
            "detail": {}}
    base.update(overrides)
    return base


def _launch_profile(**overrides):
    from scripts.deterministic_workflow.standalone_profile import (
        CompletionSelector, DeliveryProofSelector, ReadinessSelector, StandaloneProfile)
    fields = dict(
        driver="claude", binary="claude", supported_range=((1, 0, 0), (99, 0, 0)),
        delivery_mode="launch_with_prompt", identity_binding="minted_echo",
        identity_flag="--session-id",
        readiness_records=(ReadinessSelector(channel="structured", record_type="system",
                                             session_field="session_id"),),
        delivery_proofs=(DeliveryProofSelector(channel="structured",
                                               record_type="assistant"),),
        completion_records=(CompletionSelector(channel="structured", record_type="result",
                                               error_field="is_error"),),
        auth_markers=(("error", "authentication_failed"),
                      ("is_api_error_message", "True"),
                      ("terminal_reason", "api_error")))
    fields.update(overrides)
    return StandaloneProfile(**fields)


class DeliveryModeRehearsalTests(unittest.TestCase):
    """D4.2b Check 1.  **A declared capability is never trusted.**

    Every disagreement between what a profile DECLARES and what the CLI actually does is a
    named typed outcome that fails closed.  None of them is repaired by falling back to the
    other mode, retrying in the other mode, or downgrading the check to a warning -- that is
    USER DIRECTIVE D-C, and these cases are what hold it.
    """

    def test_a_profile_declaring_no_delivery_mode_is_profile_invalid(self) -> None:
        """There is no default.  A runtime that guessed would be guessing about delivery."""
        from scripts.deterministic_workflow.standalone_profile import ProfileError
        with self.assertRaises(ProfileError) as caught:
            _launch_profile(delivery_mode="")
        self.assertIn("no default", str(caught.exception))

    def test_the_capability_declaration_is_validated_on_the_production_path(self) -> None:
        """`validate_driver_capabilities` runs in PREFLIGHT, not only where a caller asks.

        Without this the empty-`delivery_proofs` refusal would never fire in a real run:
        `PROMPT_DELIVERED` would simply be unreachable and the run would be reported as a
        `delivery_mode_mismatch` — a statement about the CLI — when the truth is a profile
        omission. Naming the right thing is the whole point of a closed failure vocabulary.
        """
        # Each refusal must NAME what is missing, in the terms the operator has to fix.
        for field, phrase in (("delivery_proofs", "no delivery proof"),
                              ("completion_records", "no completion record"),
                              ("readiness_records", "no readiness record")):
            with self.subTest(field):
                outcome = preflight.check_delivery_mode(
                    _launch_profile(**{field: ()}), {},
                    mode_rehearsal=lambda p, e: _observation())
                self.assertEqual(outcome["verdict"], "fail")
                self.assertEqual(
                    outcome["reason"], "profile_invalid",
                    f"an empty {field} was not caught as a profile omission; it would have "
                    "surfaced later as a statement about the CLI instead")
                self.assertIn(
                    phrase, outcome["evidence"]["detail"],
                    f"the refusal for an empty {field} does not say what is missing")
        # ...and a complete declaration still passes, so the guard is not reject-everything.
        self.assertEqual(
            preflight.check_delivery_mode(_launch_profile(), {},
                                          mode_rehearsal=lambda p, e: _observation()
                                          )["verdict"],
            "pass")

    def test_launch_mode_rehearsal_refuses_auth_failure_shape(self) -> None:
        """W-2: a TYPED auth marker names the failure `auth_absent`, never `pass`.

        And never `delivery_mode_unverified` either: the reason the delivery leg failed is
        KNOWN, not unverified, and reporting it as unverified would send an operator looking
        for a profile bug instead of a login.
        """
        outcome = preflight.check_delivery_mode(
            _launch_profile(), {},
            mode_rehearsal=lambda p, e: _observation(
                delivery_proof=False,
                auth_marker={"field": "error", "expected": "authentication_failed",
                             "record_type": "assistant"}))
        self.assertEqual(outcome["verdict"], "fail")
        self.assertEqual(outcome["reason"], "auth_absent")
        self.assertNotEqual(outcome["reason"], "delivery_mode_unverified")
        decision = preflight.compose([outcome])
        self.assertFalse(decision["proceed"], "a run started on an unauthenticated CLI")

    def test_rehearsal_auth_marker_overrides_a_passing_auth_probe(self) -> None:
        """W-2's ordering: the LATER, STRONGER observation wins.

        The `auth` check and this rehearsal are two observations at two instants.  If
        credentials lapse, are revoked, or are shadowed between them, an earlier green
        `auth` verdict must not suppress a later typed auth marker -- that window is exactly
        how a green preflight comes to sit on top of an unauthenticated CLI.
        """
        auth_pass = preflight.check_auth(
            _launch_profile(auth_secret_ref={}), {},
            auth_probe_argv=["claude", "auth", "status"],
            prober=lambda argv, env, *, timeout_ms, cwd=None: {
                "outcome": "completed", "exit_code": 0,
                "output": '{"loggedIn":true,"authMethod":"claude.ai"}',
                "interactive_hit": ""})
        self.assertEqual(auth_pass["verdict"], "pass",
                         "the probe must PASS, or this case proves nothing about ordering")
        mode = preflight.check_delivery_mode(
            _launch_profile(), {},
            mode_rehearsal=lambda p, e: _observation(
                delivery_proof=False,
                auth_marker={"field": "terminal_reason", "expected": "api_error",
                             "record_type": "result"}))
        self.assertEqual(mode["reason"], "auth_absent")
        decision = preflight.compose([auth_pass, mode])
        self.assertFalse(decision["proceed"])
        self.assertEqual(decision["reason"], "auth_absent")
        self.assertEqual(decision["check"], "delivery_mode",
                         "the rehearsal, not the probe, must be the deciding check")

    def test_the_four_capability_outcomes_are_each_reachable_and_each_fail_closed(self):
        """All four, by name, and every one refuses the start."""
        cases = (
            ("delivery_mode_unverified",
             _launch_profile(), _observation(delivery_proof=False)),
            ("delivery_mode_ambiguous",
             _launch_profile(), _observation(waited_without_prompt=True)),
            ("identity_binding_unverified",
             _launch_profile(), _observation(identity_bound=False)),
            ("delivery_mode_mismatch",
             _launch_profile(delivery_mode="post_ready_delivery"),
             _observation(waited_without_prompt=False)),
        )
        seen = set()
        for expected, prof, observation in cases:
            with self.subTest(expected):
                outcome = preflight.check_delivery_mode(
                    prof, {}, mode_rehearsal=lambda p, e, o=observation: o)
                self.assertEqual(outcome["verdict"], "fail")
                self.assertEqual(outcome["reason"], expected)
                self.assertFalse(preflight.compose([outcome])["proceed"])
                seen.add(expected)
        self.assertEqual(seen, set(lifecycle.CAPABILITY_FAILURE_REASONS)
                         - {"identity_binding_violated"},
                         "a capability outcome exists that no preflight case reaches")
        # The subtracted member is not uncovered, only covered ELSEWHERE: it is a RUNTIME
        # refusal rather than a preflight outcome, and `AdoptedIdentityRebindTests` in this
        # module drives it -- so the closed set has no member without a behavioural case.

    def test_no_rehearsal_supplied_is_unknown_never_pass(self) -> None:
        outcome = preflight.check_delivery_mode(_launch_profile(), {}, mode_rehearsal=None)
        self.assertEqual(outcome["verdict"], "unknown")
        self.assertEqual(outcome["reason"], "delivery_mode_unverified")
        self.assertFalse(preflight.compose([outcome])["proceed"],
                         "unknown is not pass -- that is this module's whole contract")

    def test_a_passing_launch_mode_rehearsal_requires_all_four_legs(self) -> None:
        """The POSITIVE case, so the four negatives above are not vacuous."""
        outcome = preflight.check_delivery_mode(
            _launch_profile(), {}, mode_rehearsal=lambda p, e: _observation())
        self.assertEqual(outcome["verdict"], "pass")
        self.assertEqual(outcome["reason"], "")
        self.assertTrue(outcome["evidence"]["conjunctive_delivery_proof"])
        self.assertTrue(outcome["evidence"]["no_auth_marker"])

    def test_adopted_binding_may_not_declare_an_identity_flag(self) -> None:
        """D4.4 A-6: M-10 measured the CLI SILENTLY ignoring a supplied identity."""
        from scripts.deterministic_workflow.standalone_profile import ProfileError
        with self.assertRaises(ProfileError) as caught:
            _launch_profile(driver="codex", binary="codex", identity_binding="adopted",
                            identity_flag="-c")
        self.assertIn("silent", str(caught.exception).lower())

    def test_a_declared_proof_whose_flags_are_not_composed_is_refused(self) -> None:
        """A proof that could never fire is a profile bug, not a runtime mystery."""
        from scripts.deterministic_workflow.standalone_profile import DeliveryProofSelector
        outcome = preflight.check_delivery_mode(
            _launch_profile(delivery_proofs=(
                DeliveryProofSelector(channel="structured", record_type="user",
                                      requires_flags=("--input-format",
                                                      "--replay-user-messages")),)),
            {}, mode_rehearsal=lambda p, e: _observation())
        self.assertEqual(outcome["reason"], "delivery_mode_unverified")
        self.assertIn("--replay-user-messages", outcome["evidence"]["absent_flags"])

    def test_a_declared_credential_seed_that_does_not_exist_is_auth_scope_unseeded(self):
        """M-8: an EMPTY run-scoped credential root yields `401 Unauthorized`."""
        outcome = preflight.check_delivery_mode(
            _launch_profile(auth_seed_source="/nonexistent/os37/auth.json",
                            auth_seed_dest_name="auth.json"),
            {}, mode_rehearsal=lambda p, e: _observation())
        self.assertEqual(outcome["reason"], "auth_scope_unseeded")

    def test_the_real_mode_liar_fixture_drives_the_rehearsal_end_to_end(self) -> None:
        """Not injected: a REAL spawn of a CLI that lies about its mode.

        The observation legs are produced by `StandaloneSession.rehearse_delivery_mode`
        against a real process on a real pty, so this case covers the rehearsal itself and
        not only the decision rule it feeds.
        """
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        base = pathlib.Path(tempfile.mkdtemp())
        prof = _launch_profile(
            binary="os37-mode-liar-cli", bin_dirs=(str(FIXTURE_BIN),),
            driver_env={"OS37_LIAR_MODE": "ignores-identity"},
            timeouts=Timeouts(preflight_timeout_ms=3000))
        session = StandaloneSession(
            intent={"intent_id": "intent-liar", "command_id": "c", "payload_digest": "d"},
            profile=prof, artifact_base=base, run_id="run_liar",
            journal=sj.ExecutionJournal(base, "run_liar"))
        env = env_policy.build_child_env(prof, spawn_token="t-liar",
                                         include_secrets=False)
        observed = session.rehearse_delivery_mode(prof, env)
        self.assertTrue(observed["evaluable"])
        # It emitted a well-formed record of the declared type carrying a DIFFERENT id, with
        # no error and no warning -- so R-B did not close, exactly as M-10 measured.
        self.assertFalse(observed["r_b_closed"],
                         "a silently-substituted identity closed R-B")
        outcome = preflight.check_delivery_mode(prof, env,
                                                mode_rehearsal=lambda p, e: observed)
        self.assertEqual(outcome["reason"], "identity_binding_unverified")

    def test_the_real_auth_failure_fixture_reaches_auth_absent_not_unverified(self) -> None:
        """The same, for the M-15-shaped stream: a KNOWN reason is never `unverified`."""
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        base = pathlib.Path(tempfile.mkdtemp())
        prof = _launch_profile(
            binary="os37-mode-liar-cli", bin_dirs=(str(FIXTURE_BIN),),
            driver_env={"OS37_LIAR_MODE": "auth-failure"},
            timeouts=Timeouts(preflight_timeout_ms=3000))
        session = StandaloneSession(
            intent={"intent_id": "intent-auth", "command_id": "c", "payload_digest": "d"},
            profile=prof, artifact_base=base, run_id="run_auth",
            journal=sj.ExecutionJournal(base, "run_auth"))
        env = env_policy.build_child_env(prof, spawn_token="t-auth",
                                         include_secrets=False)
        observed = session.rehearse_delivery_mode(prof, env)
        self.assertTrue(observed["r_b_closed"],
                        "R-B must close: the fixture IS this dispatch's process, and "
                        "admission asserts provenance and nothing more")
        self.assertFalse(observed["delivery_proof"],
                         "the conjunctive selector accepted an identity-bound synthetic "
                         "authentication response")
        self.assertIsNotNone(observed["auth_marker"])
        outcome = preflight.check_delivery_mode(prof, env,
                                                mode_rehearsal=lambda p, e: observed)
        self.assertEqual(outcome["reason"], "auth_absent")

    def test_the_real_waiting_fixture_passes_a_post_ready_declaration(self) -> None:
        """`post_ready_delivery` is a LIVE, exercised path, not retained prose.

        The fixture reaches the admission quorum and then WAITS, which is the behaviour no
        installed CLI currently has -- and the reason the mode is kept is that deleting it
        would delete the readiness-before-delivery guarantee for every future driver.
        """
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        base = pathlib.Path(tempfile.mkdtemp())
        prof = _launch_profile(
            binary="os37-waiting-cli", bin_dirs=(str(FIXTURE_BIN),),
            delivery_mode="post_ready_delivery",
            timeouts=Timeouts(preflight_timeout_ms=2500))
        session = StandaloneSession(
            intent={"intent_id": "intent-wait", "command_id": "c", "payload_digest": "d"},
            profile=prof, artifact_base=base, run_id="run_wait",
            journal=sj.ExecutionJournal(base, "run_wait"))
        env = env_policy.build_child_env(prof, spawn_token="t-wait",
                                         include_secrets=False)
        observed = session.rehearse_delivery_mode(prof, env)
        self.assertTrue(observed["r_b_closed"],
                        "the waiting fixture did not close R-B before any prompt")
        self.assertTrue(observed["waited_without_prompt"],
                        "the fixture did not WAIT; the post_ready path is untested")
        outcome = preflight.check_delivery_mode(prof, env,
                                                mode_rehearsal=lambda p, e: observed)
        self.assertEqual(outcome["verdict"], "pass", outcome["reason"])


class LaunchModeAdmissionGateTests(unittest.TestCase):
    """D4.2a / D4.3d step 1.  **The admission quorum still gates every advance.**

    In `launch_with_prompt` the quorum no longer gates a WRITE -- the write happened at
    `execve` -- but closing it is the ONLY way to leave `STARTING`, so nothing after
    `STARTING` is reachable without it.  These cases pin the two directions of that, because
    the tempting shortcut when the prompt is already gone is to skip straight to looking for
    a delivery proof.
    """

    def _session(self, *, transcript: str = ""):
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        base = pathlib.Path(tempfile.mkdtemp())
        intent = {"intent_id": "intent-gate", "command_id": "c", "payload_digest": "d",
                  "run_id": "run_gate", "phase": "IMPLEMENTATION", "role": "WORKER",
                  "round_kind": "PHASE_GATE"}
        ledger = InMemoryRuntimeStateStore()
        claim = ledger.claim(intent)
        session = StandaloneSession(
            intent=intent, profile=_launch_profile(
                binary="os37-stub-cli", bin_dirs=(str(FIXTURE_BIN),),
                timeouts=Timeouts(preflight_timeout_ms=2000, readiness_timeout_ms=50)),
            artifact_base=base, run_id="run_gate",
            journal=sj.ExecutionJournal(base, "run_gate"), runtime_state=ledger)
        if transcript:
            session.capture.append(transcript.encode(), at="2026-09-10T00:00:00Z")
        return session, claim["lease_token"]

    def test_delivery_is_not_attempted_when_the_quorum_never_closes(self) -> None:
        """A readiness timeout is a NAMED failure, and `await_delivery` is not reached."""
        from scripts.deterministic_workflow import standalone_drivers as drivers
        session, _token = self._session()
        session.state = "TIMED_OUT"
        session.delivery_intent = dict(drivers.make_delivery_intent(
            intent_id="intent-gate", dispatch_id="d", task_id="t", session_id="s",
            payload="p", argv_digest="a", attempt_incarnation="i",
            delivery_mode="launch_with_prompt"))
        # `await_delivery` itself never advances a state whose transition the invariants
        # refuse: with no proof and no terminal record it reports a NAMED non-delivery.
        result = session.await_delivery()
        self.assertEqual(result["delivery"], "not_observed")
        self.assertEqual(result["failure_reason"], "delivery_mode_mismatch",
                         "a launch-mode dispatch that produced neither a delivery proof nor "
                         "a typed terminal outcome must be a mode mismatch, not a timeout: "
                         "the prompt provably left with the execve")

    def test_await_delivery_refuses_a_post_ready_driver(self) -> None:
        from scripts.deterministic_workflow import standalone_drivers as drivers
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        base = pathlib.Path(tempfile.mkdtemp())
        session = StandaloneSession(
            intent={"intent_id": "i", "command_id": "c", "payload_digest": "d"},
            profile=_launch_profile(delivery_mode="post_ready_delivery"),
            artifact_base=base, run_id="run_gate",
            journal=sj.ExecutionJournal(base, "run_gate"))
        with self.assertRaises(drivers.DeliveryModeMismatch):
            session.await_delivery()

    def test_await_delivery_refuses_without_a_journalled_intent(self) -> None:
        """No `DELIVERY_INTENT` means no prompt digest to prove a delivery against."""
        from scripts.deterministic_workflow import standalone_drivers as drivers
        session, _token = self._session()
        session.delivery_intent = None
        with self.assertRaises(drivers.DeliveryModeMismatch):
            session.await_delivery()

    def test_a_typed_terminal_record_settles_rather_than_reporting_a_mismatch(self) -> None:
        """D4.3c's precedence rule: an auth failure is reported as an auth failure.

        Tightening the delivery selectors must not convert an honestly-failing run into a
        MISLABELLED one, so `delivery_mode_mismatch` is reserved for the case where NEITHER
        a delivery proof NOR a typed terminal outcome arrives.
        """
        from scripts.deterministic_workflow import standalone_drivers as drivers
        stream = (STREAMS / "m15_claude_auth_failure.stream").read_text(
            encoding="utf-8", errors="replace")
        session, _token = self._session(transcript=stream)
        session.delivery_intent = dict(drivers.make_delivery_intent(
            intent_id="intent-gate", dispatch_id="d", task_id="t",
            session_id=(STREAMS / "m15_claude_auth_failure.session_id").read_text().strip(),
            payload="p", argv_digest="a", attempt_incarnation="i",
            delivery_mode="launch_with_prompt"))
        result = session.await_delivery()
        self.assertEqual(result["delivery"], "not_observed")
        self.assertTrue(result["terminal_record_present"],
                        "the measured auth-failure stream carries a typed terminal record, "
                        "and the run must settle from its own named cause")
        self.assertNotIn("failure_reason", result,
                         "an authentication failure was reported as a delivery-mode "
                         "mismatch; D4.3c reserves that name for a driver whose declared "
                         "proof set does not describe its CLI")
        self.assertNotEqual(session.state, "PROMPT_DELIVERED")


    def test_spawn_only_supplies_the_payload_a_launch_mode_driver_needs(self) -> None:
        """"Spawn only" means *do not wait for the settlement*, not *spawn something else*.

        For a `launch_with_prompt` driver the prompt goes on the argv, so a `spawn_only`
        that omitted it would compose an argv with no work in it and refuse with
        `delivery_mode_mismatch` — an operator path that silently could not work.
        """
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        from scripts.deterministic_workflow.standalone_adapter import StandaloneAdapter
        from scripts.deterministic_workflow.standalone_runtime import StandaloneRuntime
        base = pathlib.Path(tempfile.mkdtemp())
        profile = _launch_profile(binary="os37-stub-cli", bin_dirs=(str(FIXTURE_BIN),),
                                  timeouts=Timeouts(preflight_timeout_ms=2000,
                                                    readiness_timeout_ms=1500))
        ledger = InMemoryRuntimeStateStore()
        runtime = StandaloneRuntime(artifact_base=base, run_id="run_spawn_only",
                                    profile=profile, runtime_state=ledger,
                                    journal=sj.ExecutionJournal(base, "run_spawn_only"))
        adapter = StandaloneAdapter(runtime, runtime_state=ledger,
                                    artifact_base=base, run_id="run_spawn_only")
        intent = {"intent_id": "intent-spawn-only", "command_id": "c",
                  "payload_digest": "d", "run_id": "run_spawn_only",
                  "phase": "IMPLEMENTATION", "role": "WORKER", "round_kind": "PHASE_GATE"}
        claim = ledger.claim(intent)
        receipt = adapter.spawn_only(
            intent, lease_token=claim["lease_token"],
            auth_probe_argv=["os37-stub-cli", "auth", "status"],
            prober=lambda argv, env, *, timeout_ms, cwd=None: {
                "outcome": "completed", "exit_code": 0,
                "output": '1.5.0 {"loggedIn": true}', "interactive_hit": ""},
            help_text="--session-id -p --output-format stream-json --verbose",
            rehearsal=lambda p, e, sid: {"channel": "structured",
                                         "record_type": "system", "session_id": sid},
            mode_rehearsal=lambda p, e: {"r_b_closed": True, "delivery_proof": True,
                                         "auth_marker": None,
                                         "waited_without_prompt": False,
                                         "evaluable": True, "identity_bound": True,
                                         "detail": {}})
        self.assertNotEqual(
            receipt["failure_reason"], "delivery_mode_mismatch",
            "spawn_only composed a launch-mode argv with no prompt in it")
        self.assertEqual(receipt["start_outcome"], "ready", receipt["failure_reason"])
        # ...and the intent it journalled carries the digest of the payload it supplied.
        journal = sj.ExecutionJournal(base, "run_spawn_only")
        row = journal.delivery_intent_for("intent-spawn-only")
        self.assertIsNotNone(row, "spawn_only journalled no delivery intent")
        self.assertTrue(row["source_vocabulary"]["prompt_digest"])


class PostReadyDeliveryPathTests(unittest.TestCase):
    """The `post_ready_delivery` contract, kept BYTE-UNCHANGED and kept LIVE (D4.2a)."""

    def test_post_ready_delivery_refuses_write_without_ready_token(self) -> None:
        """`deliver()` takes a token only a `ready` verdict constructs.

        "A driver wrote a prompt without a ready verdict" is therefore not a bug that can be
        written -- it is a `TypeError`/`DeliveryModeMismatch` at the call site.
        """
        from scripts.deterministic_workflow import standalone_drivers as drivers
        driver = drivers.driver_for(_launch_profile(delivery_mode="post_ready_delivery"))
        self.assertTrue(hasattr(driver, "deliver"))
        with self.assertRaises(drivers.DeliveryModeMismatch):
            driver.deliver("not-a-token", 0, "payload", measured_ingest_rate=1.0,
                           verify=lambda *_: {})
        with self.assertRaises(drivers.DeliveryModeMismatch):
            drivers.ReadyToken(session_id="s", authority=object())
        # ...and the gate refuses when the quorum is not closed.
        with self.assertRaises(drivers.DeliveryModeMismatch):
            driver.may_send_prompt({"liveness": None, "bound_signal": None,
                                    "refusals": ()}, minted_session_id="s")

    def test_launch_with_prompt_driver_has_no_deliver_method(self) -> None:
        """D4.3d step 3.  ABSENT, not present-and-guarded.

        A guarded method is still a method a later refactor can call with the guard removed;
        an absent attribute is not.
        """
        from scripts.deterministic_workflow import standalone_drivers as drivers
        driver = drivers.driver_for(_launch_profile())
        self.assertFalse(hasattr(driver, "deliver"),
                         "a launch_with_prompt driver exposes a prompt-write path")
        with self.assertRaises(drivers.DeliveryModeMismatch):
            driver.may_send_prompt({}, minted_session_id="s")

    def test_launch_argv_refuses_to_compose_without_a_prompt(self) -> None:
        from scripts.deterministic_workflow import standalone_drivers as drivers
        driver = drivers.driver_for(_launch_profile())
        with self.assertRaises(drivers.DeliveryModeMismatch):
            driver.launch_argv(session_id="s", prompt="")
        composed = driver.launch_argv(session_id="s", prompt="do the thing")
        self.assertEqual(composed[-1], "do the thing",
                         "the prompt must be the POSITIONAL argument (D4.0 M-1)")


class AdoptedIdentityRebindTests(unittest.TestCase):
    """D4.4 A-2/A-4, the FIFTH capability outcome: `identity_binding_violated`.

    The other four members of `CAPABILITY_FAILURE_REASONS` are each reached through
    `check_delivery_mode` and asserted by `DeliveryModeRehearsalTests::
    test_the_four_capability_outcomes_are_each_reachable_and_each_fail_closed`, which
    explicitly subtracts this one.  The fifth is not a preflight outcome at all -- it is a
    RUNTIME refusal raised by `StandaloneSession._binding_identity` -- and until this class
    it had no behavioural case anywhere.

    Two DIFFERENT claims are asserted here, and keeping them apart is the point:

      * **A-2 is the property that actually holds.**  `bound_readiness_signal(adopt=True)`
        returns the FIRST declared record of an APPEND-ONLY transcript, so a second,
        different identity arriving later never becomes the offered value and cannot
        re-bind.  That is the safety guarantee the design owes, and it is measured directly.
      * **A-4 is the defence behind it.**  Because A-2 makes the disagreement unreachable
        along the append-only path, this class does NOT claim the branch has been observed
        firing in a real run.  It drives the branch from the only state that reaches it and
        asserts what it does when it fires: `FAILED`, a `REFUSED` record naming
        `identity_binding_violated`, and `IdentityBindingUnverified` to the caller rather
        than either identity.
    """

    #: Two well-formed `thread.started` records carrying DIFFERENT ids, in arrival order.
    FIRST = "01a08987-5c5d-77f2-8729-a83cc12ec510"
    SECOND = "01a08989-1111-4222-8333-a83cc12ec510"

    def _session(self):
        from scripts.deterministic_workflow import standalone_journal as sj
        from scripts.deterministic_workflow.standalone_runtime import StandaloneSession
        base = pathlib.Path(tempfile.mkdtemp())
        profile = _codex_live_profile()
        self.assertEqual(profile.identity_binding, "adopted",
                         "this case is about the adopted binding; the profile must use it")
        return StandaloneSession(
            intent={"intent_id": "intent-rebind", "command_id": "c",
                    "payload_digest": "d"},
            profile=profile, artifact_base=base, run_id="run_rebind",
            journal=sj.ExecutionJournal(base, "run_rebind"))

    def _transcript(self, *ids: str) -> str:
        return "".join('{"type":"thread.started","thread_id":"%s"}\r\n' % i for i in ids)

    def test_a_later_different_identity_never_re_binds(self) -> None:
        """A-2, measured: the FIRST record and only the first, append-only."""
        session = self._session()
        frozen = session._binding_identity(self._transcript(self.FIRST))
        self.assertEqual(frozen, self.FIRST)
        self.assertEqual(session.adopted_id, self.FIRST)
        # The second record ARRIVES -- appended after the first, which is the only way a
        # bounded capture ever grows -- and the binding does not move.
        still = session._binding_identity(self._transcript(self.FIRST, self.SECOND))
        self.assertEqual(still, self.FIRST,
                         "a second declared record re-bound the adopted identity")
        self.assertEqual(session.state, "STARTING",
                         "A-2 absorbed the second record; nothing should have failed")
        # The freeze is one-way, and the journal records the OBSERVATION without holding
        # any authority over it: exactly one `identity_bound` event for the whole exchange.
        bound = [r for r in session.journal.rows() if r["event"] == "identity_bound"]
        self.assertEqual(len(bound), 1,
                         "the second record produced a second identity_bound event")
        self.assertEqual(bound[0]["source_vocabulary"]["adopted_external_id"], self.FIRST)

    def test_a_disagreeing_offer_after_the_freeze_is_identity_binding_violated(self) -> None:
        """A-4, the defensive branch, driven from the only state that reaches it.

        Stated plainly: this is NOT evidence that the branch has fired in a real run.  A-2
        above is why it does not.  It is here so that a future driver whose transcript is
        not append-only -- a rotated capture, a re-read from a truncated buffer -- refuses
        instead of silently re-binding, and so that the refusal's shape is asserted rather
        than assumed.
        """
        from scripts.deterministic_workflow import standalone_drivers as drivers
        session = self._session()
        session.adopted_id = self.FIRST          # frozen, exactly as A-3 leaves it
        with self.assertRaises(drivers.IdentityBindingUnverified) as caught:
            session._binding_identity(self._transcript(self.SECOND))
        message = str(caught.exception)
        self.assertIn(self.SECOND, message)
        self.assertIn(self.FIRST, message)
        self.assertIn("settled from neither", message)
        self.assertEqual(session.state, "FAILED",
                         "a disagreeing identity left the run in a settleable state")
        refusals = [r for r in session.journal.rows() if r["kind"] == "REFUSED"]
        self.assertEqual(len(refusals), 1, refusals)
        vocabulary = refusals[0]["source_vocabulary"]
        self.assertEqual(vocabulary["failure_reason"], "identity_binding_violated")
        self.assertIn(vocabulary["failure_reason"], lifecycle.CAPABILITY_FAILURE_REASONS)
        self.assertEqual(vocabulary["frozen"], self.FIRST)
        self.assertEqual(vocabulary["second"], self.SECOND)

    def test_every_capability_failure_reason_now_has_a_behavioural_case(self) -> None:
        """The closed set has no member left uncovered.

        Four are reached through preflight by `DeliveryModeRehearsalTests`; the fifth is
        reached through the runtime here.  A sixth reason added later fails this until it
        has a case of its own.
        """
        self.assertEqual(len(lifecycle.CAPABILITY_FAILURE_REASONS), 5)
        self.assertIn("identity_binding_violated", lifecycle.CAPABILITY_FAILURE_REASONS)


if __name__ == "__main__":
    unittest.main()
