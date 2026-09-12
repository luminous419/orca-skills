"""OS-37 N2.  The child environment: CONSTRUCTED from an allowlist, never pruned.

The distinction matters more than it looks.  ``dict(os.environ)`` followed by ``del`` for
each name somebody thought of is a denylist wearing an allowlist's clothes: the next CLI
release adds a marker variable and it is inherited silently.  This module starts from an
empty dict and puts three tiers into it (DESIGN D7.1), so a name nobody enumerated is
absent by construction rather than by vigilance.

The parent process this runtime is developed inside carries, among others,
``CLAUDE_CODE_MESSAGING_TOKEN`` (a bearer credential) and ``ORCA_AGENT_HOOK_TOKEN`` with a
live callback endpoint.  Leaking either into a child agent hands it a channel back into the
supervising session.  ``ORCA_HOOK_*`` is the single most dangerous vector, which is why the
scrub assertion below RAISES: an environment that cannot be proven clean is unknown, and
this module never reduces an unknown to a success.

**Only NAMES appear in this module, in the journal, and in every fixture.**  No value of
any variable and no auth material is written anywhere -- and that includes the secret
*reference*, which names a real credential location.

Environment isolation is only half of the nested-CLI story.  The other half is descriptor
closure, which :mod:`standalone_pty` owns: ``CLAUDE_CODE_MESSAGING_SOCKET`` names a path,
but an inherited OPEN socket fd would be a second channel even with the name scrubbed.
The third leg -- on-disk config reachable through an inherited real ``HOME`` -- is NOT
fully closed and is named as such (DESIGN D7.3 leg 3, U-ENV-1, U-ENV-2, G-6).
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .standalone_profile import (ALLOWED_CONFIG_ROOT_NAMES,
                                 ALLOWED_CREDENTIAL_NAMES, ProfileError,
                                 StandaloneProfile)

# ---- Tier A: inherited verbatim, and these only ----------------------------------------
# ABSENT IN THE PARENT MEANS ABSENT IN THE CHILD.  None of these is ever defaulted to a
# guess: a fabricated `HOME` or `SHELL` is a lie the child would act on.
TIER_A_INHERITED = (
    "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ",
)

#: The base PATH every child gets, after the profile's declared `bin_dirs`.  The directory
#: holding the `orca` binary is removed from the result, and so is every directory the
#: profile did not declare -- the standalone runtime must not be able to reach Orca even by
#: accident, because the E2E's whole claim is that it does not.
TIER_B_BASE_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")

#: This repository's own prefix for the spawn token.  Named so it can never be mistaken for
#: an Orca variable.  It is DIAGNOSTIC EVIDENCE ONLY and authorizes nothing
#: (`docs/AGENT_EXECUTION_CONTRACT.md:207-210`).
SPAWN_TOKEN_ENV = "ORCA_SKILLS_STANDALONE_SPAWN_TOKEN"

# ---- the scrub assertion ---------------------------------------------------------------
#: Any surviving name starting with one of these -- outside ALLOWED_EXCEPTIONS -- makes the
#: environment unclean.  `TERM_PROGRAM` is here because the parent's value is the literal
#: string `Orca`: a CLI that branches on it would mis-detect its host, and a fabricated
#: value would be a lie, so ABSENT is the only correct disposition.
FORBIDDEN_CHILD_ENV_PREFIXES = (
    "CLAUDE", "CLAUDECODE", "CODEX", "ORCA_", "OPENCODE", "ANTHROPIC_",
    "AI_AGENT", "ECC_", "TERM_PROGRAM", "DISABLE_AUTOUPDATER",
)
#: The names that legitimately survive the prefix sweep.  DERIVED from the profile module's
#: closed credential contract rather than restated here (external review #7): there is one
#: list of admitted credential names, `standalone_profile` refuses a profile that declares
#: any other, and this allowlist is the same set plus the non-secret per-driver config roots
#: and this repository's own diagnostic spawn-token marker.
#:
#: Deriving it is the point.  The literal set used to be `{ANTHROPIC_API_KEY, CODEX_HOME,
#: <spawn token>}` while the forbidden prefixes included the whole of `CLAUDE`, so a profile
#: declaring the CLI's own documented `CLAUDE_CODE_OAUTH_TOKEN` was accepted at construction
#: and then refused at spawn as a session LEAK.  Two lists, one of which nobody updated.
#:
#: What is NOT admitted stays exactly as dangerous as it was: `CLAUDE_CODE_MESSAGING_TOKEN`,
#: `CLAUDE_CODE_MESSAGING_SOCKET`, `ORCA_*`, `ORCA_AGENT_HOOK_TOKEN` and every other
#: unenumerated `CLAUDE*` name are still absent by construction and still make
#: `assert_clean` RAISE.
ALLOWED_EXCEPTIONS = (ALLOWED_CREDENTIAL_NAMES | ALLOWED_CONFIG_ROOT_NAMES
                      | frozenset({SPAWN_TOKEN_ENV}))

#: Never set at all.  Enumerated separately from the prefix sweep so the reason is legible.
NEVER_SET = ("TERM_PROGRAM", "TERM_PROGRAM_VERSION")


class ChildEnvironmentLeak(RuntimeError):
    """A forbidden parent-session variable survived into a constructed child environment.

    Raised, not returned.  The scrub assertion is REDUNDANT by design -- if the allowlist
    works it never fires -- and its whole value is that a future CLI's new marker variable
    becomes a loud failure instead of a silent coupling to the supervising session.
    """


class SecretUnavailable(RuntimeError):
    """A declared auth secret reference could not be resolved.

    Distinct from "the child needs no credential": an unresolvable reference is unknown,
    and a spawn under an unknown credential state is refused rather than attempted.
    """


def _orca_directories(path_env: str | None = None) -> tuple[str, ...]:
    """Every directory on the PARENT's PATH that holds an ``orca`` executable.

    Resolved by asking the filesystem rather than by matching directory names: an `orca`
    installed under a directory called something else must still be removed, and a
    directory merely *called* `orca` that holds no binary need not be.
    """
    found: list[str] = []
    raw = os.environ.get("PATH", "") if path_env is None else path_env
    for entry in raw.split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry) / "orca"
        try:
            if candidate.exists():
                found.append(os.path.realpath(entry))
        except OSError:  # an unreadable PATH entry cannot be proven clean -> remove it
            found.append(os.path.realpath(entry))
    return tuple(dict.fromkeys(found))


def build_child_path(profile: StandaloneProfile, *, path_env: str | None = None) -> str:
    """The child's ``PATH``: declared ``bin_dirs`` then the base, with Orca removed.

    Rebuilt, never inherited.  The parent's PATH is read for exactly one purpose -- to
    learn which directories hold an `orca` binary so they can be excluded -- and no entry
    of it is copied into the result.
    """
    excluded = set(_orca_directories(path_env))
    ordered: list[str] = []
    for entry in tuple(profile.bin_dirs) + TIER_B_BASE_PATH:
        if not entry:
            continue
        real = os.path.realpath(entry)
        if real in excluded or entry in excluded:
            continue
        if entry not in ordered:
            ordered.append(entry)
    return os.pathsep.join(ordered)


def resolve_secrets(profile: StandaloneProfile, *,
                    resolver: Callable[[str], str] | None = None) -> dict[str, str]:
    """Resolve the profile's secret REFERENCES into ``{name: value}``.

    The returned mapping is fed straight into the child environment and is never logged,
    journalled, returned to a caller that reports, or written to an artifact.  The default
    resolver reads the parent environment variable the reference names, which is the one
    place a value may legitimately come from on a developer host; an operator deployment
    injects its own resolver.

    A reference that resolves to empty RAISES.  An empty credential is not "no credential
    configured" -- the profile said one was required -- and spawning under it would turn a
    configuration error into an interactive login prompt at run time.
    """
    resolved: dict[str, str] = {}
    for name, reference in profile.auth_secret_ref.items():
        try:
            value = resolver(reference) if resolver is not None \
                else os.environ.get(reference, "")
        except Exception as exc:  # noqa: BLE001 - any resolver failure is "unknown"
            raise SecretUnavailable(f"auth secret {name} is unresolvable") from exc
        if not isinstance(value, str) or not value:
            # The REFERENCE is deliberately absent from this message.
            raise SecretUnavailable(f"auth secret {name} resolved to nothing")
        resolved[name] = value
    return resolved


def build_child_env(profile: StandaloneProfile, *, spawn_token: str = "",
                    parent_env: Mapping[str, str] | None = None,
                    secret_resolver: Callable[[str], str] | None = None,
                    include_secrets: bool = True) -> dict[str, str]:
    """CONSTRUCT the child environment.  ``os.environ`` is never copied.

    ``include_secrets=False`` builds the same environment with Tier C's credential names
    omitted, which is what the negative fixtures and the env-name assertions use: they need
    the real policy, not a redacted imitation of it, and they must not require a credential
    to exist on the host running them.
    """
    parent = dict(os.environ if parent_env is None else parent_env)
    env: dict[str, str] = {}

    # -- Tier A ---------------------------------------------------------------------------
    for name in TIER_A_INHERITED:
        if name in parent and isinstance(parent[name], str):
            env[name] = parent[name]

    # DESIGN D7.5 / PLAN D-4: the `sandbox` branch overrides the inherited HOME with a
    # provisioned root.  Both branches exist from the start so that flipping the profile
    # requires no code change if G-6 / U-ENV-1 / U-ENV-2 come back badly.
    if profile.home_policy.kind == "sandbox":
        env["HOME"] = profile.home_policy.home_dir

    # -- Tier B: computed, never inherited -----------------------------------------------
    env["PATH"] = build_child_path(profile, path_env=parent.get("PATH", ""))
    env["TERM"] = profile.term
    # COLUMNS/LINES must AGREE with the winsize the supervisor sets on the pty; a child
    # told one geometry and given another renders into a frame that is not there.
    env["COLUMNS"] = str(profile.cols)
    env["LINES"] = str(profile.rows)
    if spawn_token:
        env[SPAWN_TOKEN_ENV] = spawn_token

    # -- Tier C: per-driver, from the profile, never inherited ---------------------------
    for name, value in profile.driver_env.items():
        env[name] = value
    if include_secrets:
        env.update(resolve_secrets(profile, resolver=secret_resolver))

    # NEVER_SET is enforced after the fact as well as by omission, because a profile's
    # `driver_env` is operator-supplied and could name one.
    for name in NEVER_SET:
        env.pop(name, None)

    assert_clean(env)
    return env


def forbidden_names(env: Mapping[str, str]) -> tuple[str, ...]:
    """Every name in ``env`` that the policy forbids.  Names only, sorted."""
    offending = [
        name for name in env
        if name not in ALLOWED_EXCEPTIONS
        and any(name.startswith(prefix) for prefix in FORBIDDEN_CHILD_ENV_PREFIXES)
    ]
    return tuple(sorted(offending))


def assert_clean(env: Mapping[str, str]) -> None:
    """RAISE :class:`ChildEnvironmentLeak` if any forbidden name survived.

    Runs in three places -- at spawn, in the conformance body, and in the E2E
    preconditions.  The message carries NAMES only; a value would defeat the redaction
    discipline this module exists to hold.
    """
    offending = forbidden_names(env)
    if offending:
        raise ChildEnvironmentLeak(
            "forbidden parent-session names survived into the child environment: "
            + ", ".join(offending))


def assert_orca_unreachable(env: Mapping[str, str]) -> None:
    """RAISE if ``orca`` is resolvable on the child's own ``PATH`` (NF-4).

    A property of the constructed environment, not of a shell trick: `shutil.which` is
    given the child's PATH explicitly rather than being asked about the parent's.
    """
    found = shutil.which("orca", path=env.get("PATH", ""))
    if found is not None:
        raise ChildEnvironmentLeak(
            f"the child PATH still resolves 'orca' at {found!r}; the standalone runtime "
            "must not be able to reach the Orca binary")


def env_digest(env: Mapping[str, str]) -> str:
    """A digest over the child environment's NAMES only.

    Names only, deliberately.  This value goes into the ownership record and the journal,
    where a digest over values would be a (weak, but real) oracle for a credential.  Its
    job is to detect that a successor is looking at a session launched under a *different
    environment shape*, and names carry that.
    """
    import hashlib
    return hashlib.sha256("\n".join(sorted(env)).encode()).hexdigest()


def describe(env: Mapping[str, str]) -> dict[str, Any]:
    """A journal/artifact-safe description: NAMES and a name-digest.  No values."""
    return {"names": sorted(env), "env_digest": env_digest(env),
            "orca_on_path": shutil.which("orca", path=env.get("PATH", "")) is not None}


def sandbox_home(profile: StandaloneProfile, root: str | os.PathLike[str]) -> StandaloneProfile:
    """Flip a profile onto DESIGN D7.5's ``sandbox`` HOME branch, rooted at ``root``.

    Provided so selecting the fallback is a configuration change, exactly as D7.5 promises,
    rather than an edit to the spawn path.
    """
    from .standalone_profile import HomePolicy
    if profile.home_policy.kind == "sandbox":
        raise ProfileError("profile is already on the sandbox HOME branch")
    return profile.with_paths(
        home_policy=HomePolicy(kind="sandbox", home_dir=str(root),
                               provisioned_auth_ref=profile.home_policy.provisioned_auth_ref))
