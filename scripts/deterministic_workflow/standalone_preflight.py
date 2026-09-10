"""OS-37 N6.  Preflight: four checks, each fail-closed, each with a NAMED outcome.

The composition rule is the point: ``start()`` proceeds only when all four verdicts are
``pass``.  Any ``fail`` **or ``unknown``** makes the start a failure with that check's
named reason.  ``unknown`` is not ``pass``, and this is the module where that has to be
true, because every G-item's uncertainty lands here:

* G-1, what a driver's binary does with no credentials -- whatever it does, the auth
  check's three outcomes are exhaustive and two of them fail.
* G-2, whether a given driver offers a ``login status`` equivalent at all -- if none does,
  the probe is a bounded non-interactive spawn whose AMBIGUITY RESOLVES TO ``fail``.
* G-3, what a driver emits for an old build -- ``version_unparsable`` and
  ``version_unsupported`` are both failures; there is no "assume supported" branch to write.

``start_unknown`` is never produced here.  An unknown precondition is a FAILURE, not an
unknown start; ``start_unknown`` is reserved for the one case it means -- the spawn was
attempted and its result cannot be established -- and that is still a failure, routed to
``LOST`` at the lifecycle layer.

The fourth check carries the load F-002 put on it.  R-B is the only accepting readiness
evidence the runtime has, so a profile whose declared selector never fires would, in a
design with a title fallback, silently degrade to accepting text.  Here it instead fails
the PROFILE check **before any spawn**, with the named reason
``profile_readiness_unverified``, on the operator's own host, at configuration time.  The
cost is real and is named as DESIGN risk DR-7: a CLI build that can declare no structured
readiness record gets no standalone run until its profile is corrected.
"""
from __future__ import annotations

import os
import re
import select
import shutil
import time
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from . import standalone_env as env_policy
from .standalone_profile import DELIVERY_MODES, StandaloneProfile, parse_version

#: The four checks, in the order they run.  Ordered cheapest-and-most-fundamental first:
#: there is no point probing auth for a binary that is not there.
#: `delivery_mode` (iteration 3, D4.2b Check 1) is the fifth check.  It runs AFTER `auth`
#: and is the LATER, STRONGER observation of the same fact: it exercises the real composed
#: argv and the real child env, which a status probe does not.
CHECKS = ("binary", "version", "auth", "profile", "delivery_mode")

#: Three-valued.  `unknown` is NOT `pass` -- that is the whole contract of this module.
VERDICTS = ("pass", "fail", "unknown")

#: The closed set of named failure reasons.  A reason outside this set is a bug, not a new
#: kind of failure: an unnamed refusal is indistinguishable from a mystery.
REASONS = (
    "binary_absent", "binary_not_executable",
    "version_unreadable", "version_unparsable", "version_unsupported",
    "auth_absent", "auth_probe_interactive", "auth_probe_unreadable",
    "profile_invalid", "profile_flag_unsupported", "profile_path_unwritable",
    "profile_readiness_unverified",
    # D4.2b's four capability-vs-reality outcomes.  Every one fails closed, and NONE is
    # repaired by falling back to the other mode, retrying in the other mode, or
    # downgrading to a warning (USER DIRECTIVE D-C).
    "delivery_mode_mismatch", "delivery_mode_unverified", "delivery_mode_ambiguous",
    "identity_binding_unverified",
    # The seeded run-scoped credential root.  D4.0 M-8 measured that an EMPTY root yields
    # `401 Unauthorized`, so "declared but not seeded" is its own named failure rather than
    # an auth mystery at run time.
    "auth_scope_unseeded",
)


class PreflightRefused(RuntimeError):
    """Preflight refused the run.  Carries the named reason and the four outcomes."""

    def __init__(self, reason: str, outcomes: Sequence[Mapping[str, Any]]) -> None:
        super().__init__(f"preflight refused: {reason}")
        self.reason = reason
        self.outcomes = tuple(dict(outcome) for outcome in outcomes)


class PreflightOutcome(TypedDict):
    check: str
    verdict: str
    reason: str          # "" only when verdict == "pass"
    evidence: dict[str, Any]


def _outcome(check: str, verdict: str, reason: str,
             evidence: Mapping[str, Any] | None = None) -> PreflightOutcome:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict {verdict!r} is not one of {VERDICTS!r}")
    if verdict == "pass" and reason:
        raise ValueError("a passing check carries no reason")
    if verdict != "pass" and reason not in REASONS:
        raise ValueError(f"reason {reason!r} is not a member of the closed set {REASONS!r}")
    return {"check": check, "verdict": verdict, "reason": reason,
            "evidence": dict(evidence or {})}


# ---- the bounded, non-interactive pty probe ---------------------------------------------
class ProbeResult(TypedDict):
    outcome: str          # `completed` | `interactive` | `timeout` | `unreadable`
    exit_code: int | None
    output: str


def probe_on_pty(argv: Sequence[str], child_env: Mapping[str, str], *,
                 timeout_ms: int, cwd: str | None = None) -> ProbeResult:
    """Run ``argv`` on a PTY the supervisor NEVER WRITES TO, bounded.

    A pty rather than a pipe, deliberately: a CLI behind a pipe often suppresses exactly
    the interactive prompt this probe exists to detect, so a pipe-based probe would report
    ``pass`` for a binary that will block on a login screen the moment it gets a terminal.

    ``interactive`` is produced when bytes matching the blocking-prompt patterns appear, and
    ``timeout`` when the bound elapses.  Both are FAILURES upstream; neither is ever a pass.
    """
    from .standalone_lifecycle import classify_refusals
    try:
        import pty as _pty
        master_fd, slave_fd = _pty.openpty()
    except OSError as exc:
        return {"outcome": "unreadable", "exit_code": None, "output": f"openpty: {exc}"}
    try:
        pid = os.fork()
    except OSError as exc:
        os.close(master_fd)
        os.close(slave_fd)
        return {"outcome": "unreadable", "exit_code": None, "output": f"fork: {exc}"}
    if pid == 0:  # pragma: no cover - the child never returns
        try:
            os.setsid()
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if cwd:
                os.chdir(cwd)
            os.closerange(3, 4096)
            os.execvpe(argv[0], list(argv), dict(child_env))
        except BaseException:
            os._exit(127)
    os.close(slave_fd)
    deadline = time.time() + (timeout_ms / 1000.0)
    chunks: list[bytes] = []
    exit_code: int | None = None
    interactive = False
    eof = False

    def _reap(blocking: bool) -> int | None:
        """Reap the probe and return its exit code, or ``None`` if it has not ended.

        Split out because the probe ends two ways -- the pty reaching EOF and ``waitpid``
        reporting the exit -- and BOTH must produce the exit code.  Treating EOF as a
        timeout was the defect: the child had exited cleanly and the probe reported that its
        version could not be read, which fails closed but for the wrong reason and makes a
        working binary indistinguishable from a hanging one.
        """
        try:
            done, status = os.waitpid(pid, 0 if blocking else os.WNOHANG)
        except OSError:
            return None
        if done != pid:
            return None
        return (os.waitstatus_to_exitcode(status)
                if hasattr(os, "waitstatus_to_exitcode") else (status >> 8))

    try:
        while time.time() < deadline:
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    data = os.read(master_fd, 65536)
                except OSError:
                    data = b""
                if not data:
                    # EOF on the master: the child closed the pty, which for a
                    # non-interactive probe means it finished.  Reap it and report the
                    # status rather than letting the deadline call this a timeout.
                    eof = True
                    exit_code = _reap(blocking=True)
                    break
                chunks.append(data)
                if classify_refusals(b"".join(chunks).decode("utf-8", "replace")):
                    interactive = True
                    break
                continue
            exit_code = _reap(blocking=False)
            if exit_code is not None:
                # Drain whatever is still buffered on the master.
                while True:
                    ready, _, _ = select.select([master_fd], [], [], 0.05)
                    if not ready:
                        break
                    try:
                        extra = os.read(master_fd, 65536)
                    except OSError:
                        break
                    if not extra:
                        break
                    chunks.append(extra)
                break
    finally:
        if exit_code is None:
            # The probe never writes to the pty, so a still-running probe is killed rather
            # than answered.  A probe that outlives its bound is not evidence.
            for sig in (15, 9):
                try:
                    os.killpg(pid, sig)
                except OSError:
                    try:
                        os.kill(pid, sig)
                    except OSError:
                        pass
                try:
                    done, status = os.waitpid(pid, os.WNOHANG)
                    if done == pid:
                        break
                except OSError:
                    break
                time.sleep(0.05)
            try:
                os.waitpid(pid, 0)
            except OSError:
                pass
        try:
            os.close(master_fd)
        except OSError:
            pass
    output = b"".join(chunks).decode("utf-8", "replace")
    if interactive:
        return {"outcome": "interactive", "exit_code": exit_code, "output": output}
    if exit_code is None:
        return {"outcome": "timeout", "exit_code": None, "output": output}
    return {"outcome": "completed", "exit_code": exit_code, "output": output}


# ---- the four checks -------------------------------------------------------------------
def check_binary(profile: StandaloneProfile,
                 child_env: Mapping[str, str]) -> PreflightOutcome:
    """Resolve the binary against the CHILD's PATH, not the parent's.

    Against the child's, deliberately.  A binary only the parent can see is a failure here,
    because the child is what will have to exec it -- and the child's PATH has had the Orca
    directory removed and every undeclared directory dropped.
    """
    resolved = shutil.which(profile.binary, path=child_env.get("PATH", ""))
    if resolved is None:
        return _outcome("binary", "fail", "binary_absent",
                        {"binary": profile.binary,
                         "searched_path_entries": len(child_env.get("PATH", "").split(os.pathsep))})
    if not os.access(resolved, os.X_OK):
        return _outcome("binary", "fail", "binary_not_executable", {"resolved": resolved})
    return _outcome("binary", "pass", "",
                    {"resolved": resolved, "realpath": os.path.realpath(resolved)})


def check_version(profile: StandaloneProfile, child_env: Mapping[str, str], *,
                  prober: Any = None) -> PreflightOutcome:
    """Run ``<binary> --version`` under the child env, bounded, and compare to the range."""
    probe = prober or probe_on_pty
    result = probe([profile.binary, "--version"], child_env,
                   timeout_ms=profile.timeouts.preflight_timeout_ms)
    if result["outcome"] in ("unreadable", "timeout"):
        return _outcome("version", "fail", "version_unreadable",
                        {"probe": result["outcome"]})
    if result["outcome"] == "interactive":
        # A binary that asks a question when asked its version is not a binary whose
        # version was read.
        return _outcome("version", "fail", "version_unreadable",
                        {"probe": "interactive"})
    version = parse_version(result["output"])
    if version is None:
        return _outcome("version", "fail", "version_unparsable",
                        {"first_bytes": result["output"][:200]})
    if not profile.version_supported(version):
        return _outcome("version", "fail", "version_unsupported",
                        {"observed": list(version),
                         "supported_range": [list(profile.supported_range[0]),
                                             list(profile.supported_range[1])]})
    return _outcome("version", "pass", "", {"observed": list(version)})


def check_auth(profile: StandaloneProfile, child_env: Mapping[str, str], *,
               auth_probe_argv: Sequence[str] | None = None,
               prober: Any = None) -> PreflightOutcome:
    """A bounded, NON-INTERACTIVE credential probe under the child env.

    Three outcomes and they are exhaustive.  Two of them fail, which is why G-1 and G-2
    can stay UNKNOWN: whatever the CLI does with no credentials, it either exits cleanly
    (pass), asks a question (``auth_probe_interactive``), or does neither in the time
    allowed (``auth_probe_unreadable``).  There is no fourth thing for it to do.

    When the profile declares credential names but none resolves, the answer is
    ``auth_absent`` and no probe is run at all -- probing under a missing credential just
    produces the login prompt we already know is coming.
    """
    declared = tuple(profile.auth_secret_ref)
    if declared:
        missing = [name for name in declared if not child_env.get(name)]
        if missing:
            return _outcome("auth", "fail", "auth_absent",
                            {"missing_env_names": sorted(missing)})
    if auth_probe_argv is None:
        # No probe verb is declared for this driver.  G-2's branch: the ambiguity resolves
        # to a check on the credential's PRESENCE only, which is a pass exactly when the
        # profile's declared names all resolved -- and `unknown` when the profile declares
        # none at all, because then nothing was verified.
        if declared:
            return _outcome("auth", "pass", "",
                            {"verified": "declared credential names all resolve",
                             "probe": "none declared (G-2 unresolved)"})
        return _outcome("auth", "unknown", "auth_probe_unreadable",
                        {"detail": "the profile declares neither a credential nor a probe; "
                                   "nothing was verified, and unknown is not pass"})
    probe = (prober or probe_on_pty)(list(auth_probe_argv), child_env,
                                     timeout_ms=profile.timeouts.preflight_timeout_ms)
    if probe["outcome"] == "interactive":
        return _outcome("auth", "fail", "auth_probe_interactive",
                        {"first_bytes": probe["output"][:200]})
    if probe["outcome"] in ("timeout", "unreadable"):
        return _outcome("auth", "fail", "auth_probe_unreadable",
                        {"probe": probe["outcome"]})
    if probe["exit_code"] != 0:
        return _outcome("auth", "fail", "auth_absent",
                        {"exit_code": probe["exit_code"],
                         "first_bytes": probe["output"][:200]})
    return _outcome("auth", "pass", "", {"exit_code": 0})


def check_profile(profile: StandaloneProfile, child_env: Mapping[str, str], *,
                  help_text: str | None = None, prober: Any = None,
                  rehearsal: Any = None,
                  minted_session_id: str = "") -> PreflightOutcome:
    """Schema, flags, paths -- and the READINESS REHEARSAL.

    The rehearsal is a bounded, non-interactive spawn of the same argv under the same child
    env with a trivial no-op prompt, asserting that a record matching the profile's declared
    ``readiness_records`` selector really appears on the structured channel carrying the
    minted session id.  A profile whose selector never fires refuses the run **before any
    spawn** with ``profile_readiness_unverified``.

    This is a preflight check and not a runtime fallback on purpose.  The alternative -- a
    title or screen-preview fallback -- is exactly what F-002 forbids, so the unknown becomes
    a refusal at configuration time instead of a guess at run time.
    """
    if not profile.readiness_records:
        return _outcome("profile", "fail", "profile_readiness_unverified",
                        {"detail": "the profile declares no readiness_records; READY has "
                                   "no accepting evidence and no text fallback exists"})
    for name, path in (("settings_path", profile.settings_path),
                       ("mcp_config_path", profile.mcp_config_path),
                       ("config_root", profile.config_root),
                       ("output_last_message_path", profile.output_last_message_path),
                       ("debug_file_path", profile.debug_file_path)):
        if not path:
            continue
        target = os.path.dirname(path) or path
        if not os.path.isdir(target):
            return _outcome("profile", "fail", "profile_path_unwritable",
                            {"field": name, "missing_directory": target})
        if not os.access(target, os.W_OK):
            return _outcome("profile", "fail", "profile_path_unwritable",
                            {"field": name, "unwritable": target})
    if profile.required_flags:
        text = help_text
        if text is None:
            probe = (prober or probe_on_pty)([profile.binary, "--help"], child_env,
                                             timeout_ms=profile.timeouts.preflight_timeout_ms)
            if probe["outcome"] != "completed":
                return _outcome("profile", "fail", "profile_flag_unsupported",
                                {"probe": probe["outcome"],
                                 "detail": "the installed binary's --help could not be read, "
                                           "so no declared flag could be confirmed"})
            text = probe["output"]
        absent = [flag for flag in profile.required_flags if flag not in text]
        if absent:
            # A version drift becomes a NAMED failure here instead of a mystery at spawn.
            return _outcome("profile", "fail", "profile_flag_unsupported",
                            {"absent_flags": sorted(absent)})
    if rehearsal is None:
        return _outcome("profile", "unknown", "profile_readiness_unverified",
                        {"detail": "no readiness rehearsal was supplied; the declared "
                                   "selector was never proven to fire, and unknown is not pass"})
    observed = rehearsal(profile, child_env, minted_session_id)
    from .standalone_lifecycle import r_b_satisfied
    declared = [selector.record_type for selector in profile.readiness_records]
    # For `adopted` the identity is the CLI's own, so R-B's EQUALITY leg is checked against
    # the value the rehearsal observed rather than against one the CLI was never told.  The
    # rest of R-B is unchanged and still required: a record of a DECLARED type, on the
    # structured channel, carrying a non-empty identity.  The equality that binds a RUN is
    # not weakened -- it is simply a run-time property (the freeze of A-3, then equality
    # forever after), and there is nothing at preflight for it to bind to.
    expected_id = minted_session_id
    if profile.identity_binding == "adopted":
        expected_id = str((observed or {}).get("session_id") or "")
    if not expected_id or not r_b_satisfied(observed, minted_session_id=expected_id,
                                            declared_record_types=declared):
        return _outcome("profile", "fail", "profile_readiness_unverified",
                        {"declared_record_types": sorted(declared),
                         "observed_record_type": (observed or {}).get("record_type", ""),
                         "observed_channel": (observed or {}).get("channel", "")})
    return _outcome("profile", "pass", "",
                    {"rehearsed_record_type": observed["record_type"],
                     "declared_record_types": sorted(declared)})


# ---- D4.2b Check 1: the delivery-mode rehearsal ------------------------------------------
class ModeRehearsalObservation(TypedDict):
    """What a mode rehearsal reports back.  Every field is an OBSERVATION, not a verdict.

    The rehearsal spawns; this module decides.  Splitting them is what lets the
    deterministic suite drive every branch by supplying a recorded stream, and lets the
    live gate drive the same branches with a real binary, WITHOUT either one owning the
    decision rule.
    """

    r_b_closed: bool                  # a declared readiness record closed R-B
    delivery_proof: bool              # the CONJUNCTIVE selector accepted a record
    auth_marker: dict[str, Any] | None    # a TYPED auth/setup marker was present
    waited_without_prompt: bool       # the no-prompt spawn reached the quorum and WAITED
    evaluable: bool                   # the rehearsal could be evaluated at all
    identity_bound: bool              # the declared identity binding was established
    detail: dict[str, Any]


def check_delivery_mode(profile: StandaloneProfile, child_env: Mapping[str, str], *,
                        mode_rehearsal: Any = None) -> PreflightOutcome:
    """A declared capability is NEVER trusted.  It is checked against actual behaviour.

    **The rule, and the three legs for `launch_with_prompt`.**  A `launch_with_prompt`
    rehearsal spawns with the no-op payload on argv and requires:

      (a) at least one record matching ``readiness_records`` closing R-B, AND
      (b) at least one record satisfying the driver's **CONJUNCTIVE** delivery selector --
          `claude_delivery_selector` / `codex_delivery_selector`, NOT a bare ``type``
          lookup, AND
      (c) NO typed authentication/setup marker ANYWHERE in the rehearsal stream.

    **Why (b) is the conjunctive selector and not a type lookup (W-1).**  `auth` and this
    rehearsal are two separate observations at two separate instants.  If credentials lapse,
    are revoked, or are shadowed by a different credential root in between, a type-only
    delivery leg would have been satisfiable **from the very authentication-failure shape
    this check exists to refuse** -- D4.0 M-15's identity-bound synthetic `assistant` record
    and M-8's `401`-leg `turn.started`.  With the conjunctive selector the window can no
    longer produce a FALSE PASS; the only remaining question is what the failure is *named*.

    **Which is what (c) answers (W-2).**  The rehearsal positively scans its own stream for
    the profile's declared TYPED markers, and a hit returns ``auth_absent`` rather than
    ``delivery_mode_unverified`` -- because the reason the delivery leg failed is KNOWN, not
    unverified.  The login TEXT is never the marker; the markers are record fields.

    **Ordering, stated so it cannot be read either way.**  This check runs AFTER `auth`, and
    on disagreement -- `auth` passed, the rehearsal saw a typed auth marker -- **the
    rehearsal wins** and preflight fails `auth_absent`.  There is no branch in which an
    earlier green `auth` verdict suppresses a later auth marker.  The converse cannot arise:
    a failing `auth` check already refuses before any spawn, so this check is not reached.

    **`delivery_mode_ambiguous` exists because of M-10's SHAPE of failure.**  A CLI that
    *ignores* an input rather than rejecting it produces a SILENT mismatch, and a check that
    only looked for the declared behaviour would pass.  So a `launch_with_prompt` rehearsal
    additionally spawns ONCE MORE with no prompt at all: if that spawn reaches the quorum
    and then WAITS, the declaration is too weak and this design does not permit silently
    taking the weaker guarantee.
    """
    if profile.delivery_mode not in DELIVERY_MODES:
        return _outcome("delivery_mode", "fail", "profile_invalid",
                        {"detail": "the profile declares no delivery_mode; there is no "
                                   "default, because a runtime that guessed would be "
                                   "guessing about whether a prompt was delivered",
                         "declared": profile.delivery_mode})
    # The capability DECLARATION is validated here, on the production path, and not only
    # where a caller happens to ask for it.  Without this the empty-`delivery_proofs` refusal
    # would never fire in a real run: `PROMPT_DELIVERED` would simply be unreachable and the
    # run would be reported as a `delivery_mode_mismatch` -- a statement about the CLI -- when
    # the truth is a profile omission.  Imported lazily because `standalone_drivers` imports
    # this module's siblings and a module-scope import would close the cycle.
    from .standalone_drivers import (CapabilityDeclarationError,
                                     validate_driver_capabilities)
    try:
        validate_driver_capabilities({
            "delivery_mode": profile.delivery_mode,
            "identity_binding": profile.identity_binding,
            "readiness_records": profile.readiness_records,
            "delivery_proofs": profile.delivery_proofs,
            "completion_records": profile.completion_records,
            "resume_channel": profile.resume_channel,
        })
    except CapabilityDeclarationError as exc:
        return _outcome("delivery_mode", "fail", "profile_invalid",
                        {"detail": str(exc), "declared": profile.delivery_mode})
    if profile.identity_binding == "adopted" and profile.identity_flag:
        return _outcome("delivery_mode", "fail", "identity_binding_unverified",
                        {"detail": "a profile declaring identity_binding='adopted' may not "
                                   "declare an identity_flag: M-10 measured the CLI "
                                   "SILENTLY ignoring a supplied identity",
                         "identity_flag": profile.identity_flag})
    if profile.auth_seed_source and not os.path.exists(profile.auth_seed_source):
        return _outcome("delivery_mode", "fail", "auth_scope_unseeded",
                        {"detail": "the declared credential seed does not exist, so the "
                                   "run-scoped root would be EMPTY -- M-8 measured that as "
                                   "401 Unauthorized",
                         "auth_seed_source": profile.auth_seed_source})
    for selector in profile.delivery_proofs:
        absent = [flag for flag in selector.requires_flags
                  if flag not in tuple(profile.extra_args) + tuple(profile.required_flags)]
        if absent:
            return _outcome("delivery_mode", "fail", "delivery_mode_unverified",
                            {"detail": "a declared delivery proof depends on argv flags the "
                                       "profile does not compose, so it could never fire",
                             "record_type": selector.record_type,
                             "absent_flags": sorted(absent)})
    if mode_rehearsal is None:
        return _outcome("delivery_mode", "unknown", "delivery_mode_unverified",
                        {"detail": "no mode rehearsal was supplied; the declared mode was "
                                   "never exercised, and unknown is not pass"})
    observed = mode_rehearsal(profile, child_env)
    if not observed.get("evaluable", False):
        return _outcome("delivery_mode", "fail", "delivery_mode_unverified",
                        {"detail": "the rehearsal could not be evaluated",
                         "observation": dict(observed.get("detail") or {})})
    if not observed.get("identity_bound", False):
        return _outcome("delivery_mode", "fail", "identity_binding_unverified",
                        {"declared": profile.identity_binding,
                         "observation": dict(observed.get("detail") or {})})
    if profile.delivery_mode == "post_ready_delivery":
        if observed.get("waited_without_prompt") and observed.get("r_b_closed"):
            return _outcome("delivery_mode", "pass", "",
                            {"declared": "post_ready_delivery",
                             "quorum_closed_before_any_write": True})
        return _outcome("delivery_mode", "fail", "delivery_mode_mismatch",
                        {"declared": "post_ready_delivery",
                         "detail": "the CLI exited, refused, or emitted zero bytes to the "
                                   "deadline without reaching the admission quorum, so it "
                                   "cannot honour the declared mode",
                         "observation": dict(observed.get("detail") or {})})
    # ---- launch_with_prompt ------------------------------------------------------------
    # (c) FIRST, and unconditionally: a typed auth marker names the failure even when the
    # other two legs also failed.  Checking it after (a)/(b) would report `unverified` for
    # a run whose reason is KNOWN.
    marker = observed.get("auth_marker")
    if marker:
        return _outcome("delivery_mode", "fail", "auth_absent",
                        {"declared": "launch_with_prompt",
                         "detail": "the rehearsal stream carried a TYPED authentication / "
                                   "setup marker; the rehearsal is the later and stronger "
                                   "observation and it OVERRIDES a passing auth probe",
                         "marker": dict(marker)})
    if not observed.get("r_b_closed", False) or not observed.get("delivery_proof", False):
        return _outcome("delivery_mode", "fail", "delivery_mode_unverified",
                        {"declared": "launch_with_prompt",
                         "r_b_closed": bool(observed.get("r_b_closed")),
                         "conjunctive_delivery_proof": bool(observed.get("delivery_proof")),
                         "observation": dict(observed.get("detail") or {})})
    if observed.get("waited_without_prompt", False):
        return _outcome("delivery_mode", "fail", "delivery_mode_ambiguous",
                        {"declared": "launch_with_prompt",
                         "detail": "the no-prompt spawn ALSO reached the admission quorum "
                                   "and then waited, so post_ready_delivery -- the stronger "
                                   "guarantee -- is available and must be declared instead",
                         "observation": dict(observed.get("detail") or {})})
    return _outcome("delivery_mode", "pass", "",
                    {"declared": "launch_with_prompt",
                     "r_b_closed": True, "conjunctive_delivery_proof": True,
                     "no_auth_marker": True, "did_not_wait_without_prompt": True})


# ---- composition -----------------------------------------------------------------------
def run_preflight(profile: StandaloneProfile, child_env: Mapping[str, str], *,
                  auth_probe_argv: Sequence[str] | None = None,
                  help_text: str | None = None, prober: Any = None,
                  rehearsal: Any = None, mode_rehearsal: Any = None,
                  minted_session_id: str = "") -> tuple[PreflightOutcome, ...]:
    """Run all five checks and return all five outcomes.

    All five always run, even after one fails.  An operator fixing a deployment wants the
    whole picture, and the cost of finishing the checks is bounded by construction.
    """
    env_policy.assert_clean(child_env)
    return (
        check_binary(profile, child_env),
        check_version(profile, child_env, prober=prober),
        check_auth(profile, child_env, auth_probe_argv=auth_probe_argv, prober=prober),
        check_profile(profile, child_env, help_text=help_text, prober=prober,
                      rehearsal=rehearsal, minted_session_id=minted_session_id),
        # LAST, and deliberately so: it is the later, stronger observation of the same auth
        # fact the third check probed, and D4.2b's ordering rule gives it precedence.
        check_delivery_mode(profile, child_env, mode_rehearsal=mode_rehearsal),
    )


def compose(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The fail-closed composition rule.  ``fail`` OR ``unknown`` refuses the start.

    Returns ``{"proceed": bool, "reason": str, "outcomes": (...)}``.  It never returns
    ``start_unknown``: that member is reserved for a spawn that was attempted, and preflight
    attempts none.
    """
    for outcome in outcomes:
        if outcome["verdict"] != "pass":
            return {"proceed": False, "reason": outcome["reason"],
                    "check": outcome["check"],
                    "outcomes": tuple(dict(o) for o in outcomes)}
    return {"proceed": True, "reason": "", "check": "",
            "outcomes": tuple(dict(o) for o in outcomes)}


def require(outcomes: Sequence[Mapping[str, Any]]) -> None:
    """RAISE :class:`PreflightRefused` unless every verdict is ``pass``."""
    decision = compose(outcomes)
    if not decision["proceed"]:
        raise PreflightRefused(decision["reason"], decision["outcomes"])


def measure_ingest_rate(*, probe_bytes: int = 4096, floor_bytes_per_ms: float = 1.0,
                        budget_ms: int = 500) -> float:
    """Measure this HOST's pty ingest rate on a THROWAWAY pty.  Never the agent's.

    `docs/ORCA_RUNTIME_PRIMITIVES.md` C11: Orca's own paste constants "must be re-measured,
    not transcribed as truth".  The measurement is conservative -- a floor, and no ceiling --
    because the value feeds a settle delay whose whole purpose is not to be too short, and
    there is no ``min(...)`` anywhere on it.

    **It measures a pty this function creates and destroys, and it never writes to the
    agent's.**  Two things went wrong when it did, and both were serious.  (1) Writing four
    kilobytes of padding into a live agent's stdin IS INPUT: the agent would read it as part
    of the prompt, or as an answer to a prompt it was showing.  (2) The write BLOCKS forever
    when the child is not currently reading its stdin, so the whole runtime hung in the
    prompt-delivery path -- an unbounded hang, which is worse than any fail-closed refusal
    because nothing is reported at all.

    A throwaway pty measures the same thing the agent's would: kernel pty throughput on this
    host.  The write is non-blocking and bounded, and if it cannot complete the answer is the
    conservative floor rather than a hang.
    """
    import fcntl as _fcntl
    import pty as _pty
    try:
        master_fd, slave_fd = _pty.openpty()
    except OSError:
        return floor_bytes_per_ms
    try:
        # Non-blocking, so a full buffer returns EAGAIN instead of parking this thread.
        flags = _fcntl.fcntl(master_fd, _fcntl.F_GETFL)
        _fcntl.fcntl(master_fd, _fcntl.F_SETFL, flags | os.O_NONBLOCK)
        payload = b"\x00" * probe_bytes
        deadline = time.time() + budget_ms / 1000.0
        started = time.perf_counter()
        written = 0
        while written < len(payload) and time.time() < deadline:
            try:
                written += os.write(master_fd, payload[written:])
            except BlockingIOError:
                # The slave side is not draining.  Read it off and keep going; this is a
                # throwaway pty, so the bytes are ours to discard.
                try:
                    os.read(slave_fd, 65_536)
                except (BlockingIOError, OSError):
                    break
            except OSError:
                break
        elapsed_ms = max((time.perf_counter() - started) * 1000.0, 1e-6)
        if written <= 0:
            return floor_bytes_per_ms
        return max(written / elapsed_ms, floor_bytes_per_ms)
    finally:
        for fd in (master_fd, slave_fd):
            try:
                os.close(fd)
            except OSError:
                pass
