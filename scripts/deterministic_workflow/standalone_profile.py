"""OS-37 N1.  The typed launch PROFILE: pure data plus validation, nothing else.

AC-37-03 requires that the standalone runtime be driven by explicit configuration rather
than by a built-in table of CLIs.  This module is that configuration's shape.  It performs
no process action, reads no environment, resolves no secret and touches no filesystem;
every one of those belongs to a module that can be tested for refusing.

Two things are deliberately absent:

*A secret VALUE.*  ``auth_secret_ref`` names *where* a credential comes from.  The value is
resolved at spawn by :mod:`standalone_env`, is never logged, never journalled and never
written into an artifact.  A profile is a thing an operator commits to a repository; a
profile that could hold a key would eventually hold one.

*A hard-coded flag table.*  Every flag a driver depends on is declared here, so
:mod:`standalone_preflight` can re-read the installed binary's ``--help`` and refuse a
version drift by name (``profile_flag_unsupported``) instead of failing mysteriously at
run time.  U7 -- what the 43-CLI Orca table actually contains -- is routed around by this
design rather than resolved, and stays UNKNOWN.

``readiness_records`` is the load-bearing field.  It is the ONLY source of accepting
readiness evidence in the whole runtime (DESIGN D5.3(4) R-B): a typed record on the CLI's
own structured channel, of a declared type, carrying the session identity this runtime
minted before the spawn.  A profile that declares none can never reach ``READY``; that is
refused at preflight by ``profile_readiness_unverified`` rather than degraded to reading a
terminal title, and the cost is named as DESIGN risk DR-7.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

# ---- closed vocabularies ---------------------------------------------------------------
#: Which driver a profile configures.  This module names the two the MVP supports because
#: a profile must be *validated* against a closed set somewhere; the CLI-specific argv,
#: flags and parsing all live in `standalone_drivers`, which is the only module allowed to
#: know what those names mean (DESIGN D4.1).
DRIVER_KINDS = ("claude", "codex")
DriverKind = Literal["claude", "codex"]

#: How the child's ``HOME`` is chosen.  PLAN DECISION D-4 defaults to `inherit`; the
#: `sandbox` branch exists from the start so that if the G-6 / U-ENV-1 / U-ENV-2 fixtures
#: show a Tier-C flag does not suppress what it claims, the profile flips and NO code
#: changes (DESIGN D7.5).
HOME_POLICIES = ("inherit", "sandbox")

#: Where a readiness record may be read FROM.  `structured` is the CLI's own machine
#: channel.  There is deliberately no `screen` or `title` member: a channel this
#: vocabulary cannot name is a channel readiness cannot be accepted on.
READINESS_CHANNELS = ("structured",)

#: DESIGN D4.2a.  The DELIVERY-MODE axis: a CLOSED two-member DRIVER CAPABILITY, declared
#: by the profile and consumed only by the driver and the lifecycle transition predicates.
#: It is NOT a policy branch -- nothing in `graph.py`, `routing.py`, `executor.py`,
#: `state.py` or any decision/review module ever reads it, and
#: `test_os37_driver_isolation.py` proves that mechanically.
#:
#: `post_ready_delivery`  the CLI can be started with NO prompt, reach an observable state,
#:                        and accept the prompt afterwards.  This is the mode that carries
#:                        the readiness-before-delivery guarantee, and it is retained as a
#:                        live, tested path (the `os37-waiting-cli` fixture) precisely so
#:                        that the guarantee is not deleted along with the CLIs that cannot
#:                        currently honour it.
#: `launch_with_prompt`   the prompt MUST be supplied at process creation; the process
#:                        begins working immediately and has no state in which it waits for
#:                        one.  MEASURED for both installed CLIs (DESIGN D4.0 M-6, M-9):
#:                        zero bytes are emitted until the prompt arrives.
#:
#: There is deliberately NO DEFAULT.  A profile that does not declare a member is
#: `profile_invalid`; a runtime that guessed would be guessing about whether a prompt was
#: delivered.
DELIVERY_MODES = ("launch_with_prompt", "post_ready_delivery")
DeliveryMode = Literal["launch_with_prompt", "post_ready_delivery"]

#: DESIGN D5.3(4) / D4.4 A-1..A-6.  How R-B binds a structured record to THIS dispatch.
#:
#: `minted_echo`  the runtime mints the identity BEFORE the spawn, passes it on argv, and
#:                compares the echoed value by EQUALITY (Claude, D4.0 M-1/M-2/M-4).
#: `adopted`      the CLI mints its own identity and exposes no caller-supplied channel
#:                (Codex, D4.0 M-7/M-10).  The binding is then channel provenance plus an
#:                irrevocable freeze into the runtime-state receipt, and every later
#:                observation is compared by equality against the frozen value.
IDENTITY_BINDINGS = ("minted_echo", "adopted")
IdentityBinding = Literal["minted_echo", "adopted"]

#: Whether this driver has a real resume channel.  `external_resume` is declared from this
#: and from nothing else, so the capability cannot be more optimistic than the argv.
RESUME_CHANNELS = ("none", "cli_resume_subcommand")

#: Named truncation causes, so a bounded capture reports WHY it stopped.
TRUNCATION_CAUSES = ("total_bytes", "line_bytes", "record_count")


class ProfileError(ValueError):
    """A profile is malformed.  Raised at construction, never carried as a flag."""


_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def parse_version(text: Any) -> tuple[int, int, int] | None:
    """``(major, minor, patch)`` from anywhere in ``text``, or ``None``.

    ``None`` means *unparsable*, which :mod:`standalone_preflight` turns into the named
    failure ``version_unparsable``.  It is never "assume supported": G-3 -- what each CLI
    emits for an old or unsupported build -- is UNKNOWN, and both of the outcomes a
    version check can have that are not a clean parse-and-match are failures.
    """
    if not isinstance(text, str):
        return None
    for token in re.split(r"[\s,;()]+", text.strip()):
        match = _VERSION.match(token.lstrip("vV"))
        if match:
            return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None


@dataclass(frozen=True)
class ReadinessSelector:
    """One declared accepting readiness record (DESIGN D5.3(4) R-B).

    ``record_type`` is matched by EQUALITY against the parsed record's ``type``, and
    ``session_field`` names the field that must equal the session identity this runtime
    minted before the spawn -- also by equality.  Neither is a pattern.  A pattern would
    match a frame that merely quotes the value; equality against a locally minted
    identifier cannot be satisfied by a frame the runtime did not cause.
    """

    channel: str
    record_type: str
    session_field: str

    def __post_init__(self) -> None:
        if self.channel not in READINESS_CHANNELS:
            raise ProfileError(
                f"readiness channel {self.channel!r} is not one of {READINESS_CHANNELS!r}; "
                "a title or screen channel is not declarable")
        if not isinstance(self.record_type, str) or not self.record_type:
            raise ProfileError("readiness record_type must be a non-empty string")
        if not isinstance(self.session_field, str) or not self.session_field:
            raise ProfileError("readiness session_field must be a non-empty string")


@dataclass(frozen=True)
class DeliveryProofSelector:
    """One declared admissible DELIVERY proof record (DESIGN D4.3c class A / class B).

    Declaring a record TYPE is necessary and, since iteration 4's F-001, deliberately NOT
    sufficient: the driver additionally applies a CONJUNCTIVE predicate over the record's
    measured fields (`claude_delivery_selector` / `codex_delivery_selector`).  The reason
    is measured, not theoretical -- D4.0 M-15 recorded a pure login failure emitting an
    `assistant` record carrying the runtime's own minted session id, and D4.0 M-8 recorded
    a 401 leg emitting `turn.started`.  A type-only membership test would have admitted
    both and entered `PROMPT_DELIVERED` on an unauthenticated CLI.

    ``item_type`` narrows a container record whose kind lives one level down (Codex's
    ``item.completed`` carries ``item.type`` ``agent_message`` on the success leg and
    ``error`` on the failure leg -- M-8 measured both).

    ``requires_flags`` names argv flags the proof depends on.  A profile that declares a
    proof whose flags it does not compose is refused by D4.2b's conformance check rather
    than silently never firing.
    """

    channel: str
    record_type: str
    item_type: str = ""
    requires_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.channel not in READINESS_CHANNELS:
            raise ProfileError(
                f"delivery proof channel {self.channel!r} is not one of "
                f"{READINESS_CHANNELS!r}; a screen reading is not a delivery proof")
        if not isinstance(self.record_type, str) or not self.record_type:
            raise ProfileError("delivery proof record_type must be a non-empty string")
        if not isinstance(self.item_type, str):
            raise ProfileError("delivery proof item_type must be a string")
        if not isinstance(self.requires_flags, tuple) or \
                not all(isinstance(flag, str) and flag for flag in self.requires_flags):
            raise ProfileError("delivery proof requires_flags must be a tuple of strings")


@dataclass(frozen=True)
class CompletionSelector:
    """One declared admissible COMPLETION record (DESIGN D4.4).

    A completion record is never sufficient on its own: D4.4's rule is conjunctive and
    additionally requires the error field to be false and a `waitpid`-sourced exit the
    profile's table maps to `succeeded`.  M-2 measured why -- Claude's authentication
    failure is `type='result' subtype='success' is_error=True terminal_reason='api_error'`
    with `rc=1`, so a selector keyed on the record type or on `subtype` alone reads a login
    failure as a completed turn.
    """

    channel: str
    record_type: str
    #: The field that must be FALSE for this record to be a success candidate.  Empty means
    #: the record type carries no error field and the exit status is the only other leg.
    error_field: str = ""
    #: Values of `success_field` the profile accepts.  Empty means "not consulted".
    success_field: str = ""
    success_values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.channel not in READINESS_CHANNELS:
            raise ProfileError(
                f"completion channel {self.channel!r} is not one of {READINESS_CHANNELS!r}")
        if not isinstance(self.record_type, str) or not self.record_type:
            raise ProfileError("completion record_type must be a non-empty string")
        for name in ("error_field", "success_field"):
            if not isinstance(getattr(self, name), str):
                raise ProfileError(f"completion {name} must be a string")
        if self.success_field and not self.success_values:
            raise ProfileError(
                "a completion selector naming success_field must declare success_values; "
                "an empty accepted set would accept every value")


@dataclass(frozen=True)
class CaptureLimits:
    """Bounded output capture limits (AC-37-05, DESIGN D3.3).

    Every limit is explicit and every one has a NAMED truncation cause.  There is no
    "wrap" behaviour: silently dropping the beginning of a transcript makes a later
    completion question unanswerable while looking answerable.
    """

    max_total_bytes: int = 8 * 1024 * 1024
    max_line_bytes: int = 64 * 1024
    max_records: int = 200_000
    read_chunk: int = 65_536

    def __post_init__(self) -> None:
        for name in ("max_total_bytes", "max_line_bytes", "max_records", "read_chunk"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ProfileError(f"capture limit {name} must be a positive int, got {value!r}")
        if self.max_line_bytes > self.max_total_bytes:
            raise ProfileError("max_line_bytes cannot exceed max_total_bytes")


@dataclass(frozen=True)
class Timeouts:
    """Every bound the runtime waits under.

    These are STARTING VALUES to re-measure on the host, never truths to transcribe
    (`docs/ORCA_RUNTIME_PRIMITIVES.md` C11).  They are profile fields precisely so that no
    call site hard-codes one, and so an operator whose host is slower changes configuration
    rather than code.

    ``settle_floor_ms`` is a FLOOR, and the paste settle it contributes to is deliberately
    UNCAPPED: a `min(...)` on that value truncates the gate that keeps a long paste frame
    from losing its beginning (DESIGN D4.3 step 4).
    """

    readiness_timeout_ms: int = 60_000
    delivery_verify_timeout_ms: int = 15_000
    settle_floor_ms: int = 120
    graceful_force_timeout_ms: int = 5_000
    physical_exit_timeout_ms: int = 8_000
    force_retry_ms: int = 250
    preflight_timeout_ms: int = 20_000
    staleness_budget_ms: int = 1_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:  # noqa: PLC0206 - dataclass introspection
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ProfileError(f"timeout {name} must be a positive int, got {value!r}")


@dataclass(frozen=True)
class HomePolicy:
    """The closed union of DESIGN D7.5.  ``inherit`` is PLAN D-4's default."""

    kind: str = "inherit"
    home_dir: str = ""
    provisioned_auth_ref: str = ""

    def __post_init__(self) -> None:
        if self.kind not in HOME_POLICIES:
            raise ProfileError(f"home policy {self.kind!r} is not one of {HOME_POLICIES!r}")
        if self.kind == "sandbox" and not self.home_dir:
            raise ProfileError("home policy 'sandbox' requires home_dir")
        if self.kind == "inherit" and (self.home_dir or self.provisioned_auth_ref):
            raise ProfileError("home policy 'inherit' takes no home_dir/provisioned_auth_ref")


@dataclass(frozen=True)
class StandaloneProfile:
    """Everything the standalone runtime needs to launch ONE driver, and nothing else."""

    driver: str
    binary: str
    supported_range: tuple[tuple[int, int, int], tuple[int, int, int]]
    bin_dirs: tuple[str, ...] = ()
    #: Flags the driver's argv depends on.  Preflight asserts each appears in the installed
    #: binary's `--help`, so a version drift is a named refusal rather than a mystery.
    required_flags: tuple[str, ...] = ()
    #: The ONLY source of accepting readiness evidence.  Empty is legal to CONSTRUCT and
    #: is refused at preflight (`profile_readiness_unverified`) -- refusing here would make
    #: the fail-closed path untestable.
    readiness_records: tuple[ReadinessSelector, ...] = ()
    #: `{env-name: reference}`.  A REFERENCE, resolved at spawn.  Never a value.
    auth_secret_ref: Mapping[str, str] = field(default_factory=dict)
    #: Extra env names the driver computes, mapped to their values.  Values here are
    #: non-secret by contract; secrets go through `auth_secret_ref`.
    driver_env: Mapping[str, str] = field(default_factory=dict)
    settings_path: str = ""
    mcp_config_path: str = ""
    config_root: str = ""
    output_last_message_path: str = ""
    debug_file_path: str = ""
    sandbox_mode: str = ""
    permission_mode: str = ""
    add_dirs: tuple[str, ...] = ()
    worktree: str = ""
    term: str = "xterm-256color"
    rows: int = 40
    cols: int = 120
    home_policy: HomePolicy = field(default_factory=HomePolicy)
    capture: CaptureLimits = field(default_factory=CaptureLimits)
    timeouts: Timeouts = field(default_factory=Timeouts)
    #: `{exit code: lost_reason-or-outcome}`.  An EMPTY table is a valid, fail-closed
    #: configuration: G-7 is UNKNOWN, and an unmapped code becomes LOST with
    #: `lost_reason="exit_code_unmapped"` rather than `exited{0}`.
    exit_code_map: Mapping[int, str] = field(default_factory=dict)
    #: An optional in-band graceful hint, used ONLY as interrupt rung 0 and never as
    #: evidence of death (DESIGN D4.6).
    graceful_hint: bytes = b""
    #: Extra argv the operator appends.  Validated for shape only.
    extra_args: tuple[str, ...] = ()
    #: Set when the CLI supports suppressing session persistence.
    no_session_persistence: bool = False

    # ---- DESIGN D4.2a: the declared driver-capability axis -----------------------------
    #: A member of :data:`DELIVERY_MODES`.  NO DEFAULT: the empty string is refused at
    #: construction, so a profile cannot arrive at the runtime without having declared how
    #: its prompt reaches the process.
    delivery_mode: str = ""
    #: A member of :data:`IDENTITY_BINDINGS`.  Also has no default, for the same reason:
    #: guessing `minted_echo` against a CLI that mints its own id (M-10) fails SILENTLY.
    identity_binding: str = ""
    #: The argv flag that carries the minted identity, for `minted_echo` only.  A profile
    #: declaring `adopted` must leave it EMPTY -- D4.4 A-6: Codex silently ignores
    #: `-c thread_id=` (M-10), so a profile claiming both would be silently wrong.
    identity_flag: str = ""
    #: The ONLY source of admissible delivery proofs.  Empty is legal to CONSTRUCT and is
    #: refused by `validate_driver_capabilities` and at preflight, exactly as
    #: `readiness_records` is -- refusing here would make the fail-closed path untestable.
    delivery_proofs: tuple[DeliveryProofSelector, ...] = ()
    #: The declared completion candidates (D4.4).  Conjunctive with the exit table.
    completion_records: tuple[CompletionSelector, ...] = ()
    #: A member of :data:`RESUME_CHANNELS`.  `external_resume` is declared from THIS and
    #: from nothing else (D11.3).
    resume_channel: str = "none"
    #: The `codex exec resume` argv contract is DIFFERENT from `codex exec`'s -- M-11
    #: measured that it rejects `-C/--cd` and `--color` with `rc=2`.  So the resume argv is
    #: composed from its own field and validated against its own `--help`.
    resume_args: tuple[str, ...] = ()
    #: A credential file to SEED into the run-scoped config root, `0600`.  M-8 measured
    #: that an empty `CODEX_HOME` yields `401 Unauthorized`; nothing else from the real
    #: home is copied.  A REFERENCE to a path, never a secret value, and it is never
    #: journalled and never logged.
    auth_seed_source: str = ""
    auth_seed_dest_name: str = ""
    #: Typed authentication/setup markers the D4.2b W-2 rehearsal scan looks for.  Each is
    #: a `(dotted-field, expected-value)` pair over a PARSED record.  The login TEXT is
    #: never a marker -- these are record fields.
    auth_markers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.driver not in DRIVER_KINDS:
            raise ProfileError(f"driver {self.driver!r} is not one of {DRIVER_KINDS!r}")
        if not isinstance(self.binary, str) or not _SAFE_NAME.match(self.binary or ""):
            raise ProfileError(f"binary {self.binary!r} is not a plain executable name")
        low, high = self.supported_range
        for bound in (low, high):
            if (not isinstance(bound, tuple) or len(bound) != 3
                    or not all(isinstance(part, int) for part in bound)):
                raise ProfileError(f"supported_range bound {bound!r} is not a 3-int tuple")
        if low > high:
            raise ProfileError(f"supported_range {self.supported_range!r} is inverted")
        for name in tuple(self.auth_secret_ref) + tuple(self.driver_env):
            if not isinstance(name, str) or not name:
                raise ProfileError("env names must be non-empty strings")
        for selector in self.readiness_records:
            if not isinstance(selector, ReadinessSelector):
                raise ProfileError("readiness_records must hold ReadinessSelector values")
        if not isinstance(self.rows, int) or not isinstance(self.cols, int) \
                or self.rows <= 0 or self.cols <= 0:
            raise ProfileError("rows/cols must be positive ints")
        if not isinstance(self.graceful_hint, bytes):
            raise ProfileError("graceful_hint must be bytes")
        if self.delivery_mode not in DELIVERY_MODES:
            raise ProfileError(
                f"delivery_mode {self.delivery_mode!r} is not one of {DELIVERY_MODES!r}; "
                "there is no default -- a profile that does not declare how its prompt "
                "reaches the process is profile_invalid (DESIGN D4.2a)")
        if self.identity_binding not in IDENTITY_BINDINGS:
            raise ProfileError(
                f"identity_binding {self.identity_binding!r} is not one of "
                f"{IDENTITY_BINDINGS!r}; there is no default (DESIGN D4.4 A-1..A-6)")
        if self.identity_binding == "adopted" and self.identity_flag:
            raise ProfileError(
                "a profile declaring identity_binding='adopted' may not also declare "
                f"identity_flag {self.identity_flag!r}: D4.0 M-10 measured that the CLI "
                "SILENTLY ignores a supplied identity, so the two together are a silent "
                "lie (DESIGN D4.4 A-6)")
        if self.identity_binding == "minted_echo" and not self.identity_flag:
            raise ProfileError(
                "a profile declaring identity_binding='minted_echo' must name the argv "
                "flag that carries the minted value; otherwise R-B compares by equality "
                "against a value the CLI was never told")
        for selector in self.delivery_proofs:
            if not isinstance(selector, DeliveryProofSelector):
                raise ProfileError(
                    "delivery_proofs must hold DeliveryProofSelector values")
        for selector in self.completion_records:
            if not isinstance(selector, CompletionSelector):
                raise ProfileError(
                    "completion_records must hold CompletionSelector values")
        if self.resume_channel not in RESUME_CHANNELS:
            raise ProfileError(
                f"resume_channel {self.resume_channel!r} is not one of {RESUME_CHANNELS!r}")
        if bool(self.auth_seed_source) != bool(self.auth_seed_dest_name):
            raise ProfileError(
                "auth_seed_source and auth_seed_dest_name are declared together or not at "
                "all; a source with no destination name would seed nothing and a "
                "destination with no source would silently create an EMPTY credential root")
        for marker in self.auth_markers:
            if (not isinstance(marker, tuple) or len(marker) != 2
                    or not all(isinstance(part, str) for part in marker)):
                raise ProfileError(
                    f"auth marker {marker!r} must be a (field, expected-value) string pair")

    # -- queries -------------------------------------------------------------------------
    def version_supported(self, version: tuple[int, int, int] | None) -> bool:
        """Whether ``version`` is inside the declared range.

        ``None`` -- unparsable -- is NOT supported.  There is no branch in which an
        unreadable version is treated as acceptable.
        """
        if version is None:
            return False
        low, high = self.supported_range
        return low <= version <= high

    def with_paths(self, **paths: str) -> StandaloneProfile:
        """A copy with run-scoped paths filled in.  Profiles are frozen by design."""
        unknown = set(paths) - set(self.__dataclass_fields__)
        if unknown:
            raise ProfileError(f"unknown profile fields {sorted(unknown)!r}")
        return replace(self, **paths)

    def redacted(self) -> dict[str, Any]:
        """A journal/artifact-safe view: env NAMES only, never values or references.

        The reference itself is withheld too.  A reference like ``keychain:prod-key`` names
        a real secret location, and DESIGN's redaction discipline is that only names travel.
        """
        return {
            "driver": self.driver, "binary": self.binary,
            "supported_range": [list(self.supported_range[0]), list(self.supported_range[1])],
            "auth_secret_env_names": sorted(self.auth_secret_ref),
            "driver_env_names": sorted(self.driver_env),
            "readiness_record_types": sorted(s.record_type for s in self.readiness_records),
            "home_policy": self.home_policy.kind,
            "required_flags": list(self.required_flags),
            "delivery_mode": self.delivery_mode,
            "identity_binding": self.identity_binding,
            "delivery_proof_record_types": sorted(
                s.record_type for s in self.delivery_proofs),
            "completion_record_types": sorted(
                s.record_type for s in self.completion_records),
            "resume_channel": self.resume_channel,
            "auth_seed_declared": bool(self.auth_seed_source),
            "rows": self.rows, "cols": self.cols, "term": self.term,
        }


def _as_tuple(value: Any, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ProfileError(f"{what} must be a list of strings, got {value!r}")
    for item in value:
        if not isinstance(item, str):
            raise ProfileError(f"{what} must be a list of strings, got {item!r}")
    return tuple(value)


def profile_from_mapping(spec: Any) -> StandaloneProfile:
    """Build a profile from a plain JSON-shaped mapping, or RAISE.

    Every unknown key is refused.  A silently ignored key is how an operator comes to
    believe a setting is in force when it is not -- and for this runtime that setting might
    be the one that isolates a credential.
    """
    if not isinstance(spec, Mapping):
        raise ProfileError("profile specification must be a mapping")
    known = {
        "driver", "binary", "supported_range", "bin_dirs", "required_flags",
        "readiness_records", "auth_secret_ref", "driver_env", "settings_path",
        "mcp_config_path", "config_root", "output_last_message_path", "debug_file_path",
        "sandbox_mode", "permission_mode", "add_dirs", "worktree", "term", "rows", "cols",
        "home_policy", "capture", "timeouts", "exit_code_map", "graceful_hint",
        "extra_args", "no_session_persistence",
        "delivery_mode", "identity_binding", "identity_flag", "delivery_proofs",
        "completion_records", "resume_channel", "resume_args",
        "auth_seed_source", "auth_seed_dest_name", "auth_markers",
    }
    unknown = set(spec) - known
    if unknown:
        raise ProfileError(f"unknown profile keys {sorted(unknown)!r}")
    rng = spec.get("supported_range")
    if not isinstance(rng, Sequence) or len(rng) != 2:
        raise ProfileError("supported_range must be a two-element list")
    bounds = tuple(tuple(int(part) for part in bound) for bound in rng)  # type: ignore[arg-type]
    selectors = tuple(
        ReadinessSelector(channel=item.get("channel", "structured"),
                          record_type=item.get("record_type", ""),
                          session_field=item.get("session_field", ""))
        for item in (spec.get("readiness_records") or ())
        if isinstance(item, Mapping) or _raise_selector(item))
    delivery_proofs = tuple(
        DeliveryProofSelector(channel=item.get("channel", "structured"),
                              record_type=item.get("record_type", ""),
                              item_type=item.get("item_type", ""),
                              requires_flags=_as_tuple(item.get("requires_flags"),
                                                       "requires_flags"))
        for item in (spec.get("delivery_proofs") or ())
        if isinstance(item, Mapping) or _raise_selector(item))
    completion_records = tuple(
        CompletionSelector(channel=item.get("channel", "structured"),
                           record_type=item.get("record_type", ""),
                           error_field=item.get("error_field", ""),
                           success_field=item.get("success_field", ""),
                           success_values=_as_tuple(item.get("success_values"),
                                                    "success_values"))
        for item in (spec.get("completion_records") or ())
        if isinstance(item, Mapping) or _raise_selector(item))
    markers = tuple(
        (str(item[0]), str(item[1]))
        for item in (spec.get("auth_markers") or ())
        if isinstance(item, Sequence) and not isinstance(item, str) and len(item) == 2)
    home = spec.get("home_policy") or {}
    capture = spec.get("capture") or {}
    timeouts = spec.get("timeouts") or {}
    hint = spec.get("graceful_hint") or b""
    return StandaloneProfile(
        driver=spec.get("driver", ""), binary=spec.get("binary", ""),
        supported_range=(bounds[0], bounds[1]),  # type: ignore[arg-type]
        bin_dirs=_as_tuple(spec.get("bin_dirs"), "bin_dirs"),
        required_flags=_as_tuple(spec.get("required_flags"), "required_flags"),
        readiness_records=selectors,
        auth_secret_ref=dict(spec.get("auth_secret_ref") or {}),
        driver_env=dict(spec.get("driver_env") or {}),
        settings_path=spec.get("settings_path", ""),
        mcp_config_path=spec.get("mcp_config_path", ""),
        config_root=spec.get("config_root", ""),
        output_last_message_path=spec.get("output_last_message_path", ""),
        debug_file_path=spec.get("debug_file_path", ""),
        sandbox_mode=spec.get("sandbox_mode", ""),
        permission_mode=spec.get("permission_mode", ""),
        add_dirs=_as_tuple(spec.get("add_dirs"), "add_dirs"),
        worktree=spec.get("worktree", ""), term=spec.get("term", "xterm-256color"),
        rows=int(spec.get("rows", 40)), cols=int(spec.get("cols", 120)),
        home_policy=HomePolicy(**home) if isinstance(home, Mapping) else HomePolicy(),
        capture=CaptureLimits(**capture) if isinstance(capture, Mapping) else CaptureLimits(),
        timeouts=Timeouts(**timeouts) if isinstance(timeouts, Mapping) else Timeouts(),
        exit_code_map={int(k): str(v) for k, v in (spec.get("exit_code_map") or {}).items()},
        graceful_hint=hint if isinstance(hint, bytes) else str(hint).encode(),
        extra_args=_as_tuple(spec.get("extra_args"), "extra_args"),
        no_session_persistence=bool(spec.get("no_session_persistence", False)),
        # No `.get(..., <a mode>)` default anywhere below: an absent declaration reaches
        # `StandaloneProfile.__post_init__` as `""` and is refused there by name, which is
        # what makes an undeclared delivery mode `profile_invalid` rather than a guess.
        delivery_mode=spec.get("delivery_mode", ""),
        identity_binding=spec.get("identity_binding", ""),
        identity_flag=spec.get("identity_flag", ""),
        delivery_proofs=delivery_proofs,
        completion_records=completion_records,
        resume_channel=spec.get("resume_channel", "none"),
        resume_args=_as_tuple(spec.get("resume_args"), "resume_args"),
        auth_seed_source=spec.get("auth_seed_source", ""),
        auth_seed_dest_name=spec.get("auth_seed_dest_name", ""),
        auth_markers=markers,
    )


def _raise_selector(item: Any) -> bool:
    raise ProfileError(f"readiness record {item!r} must be a mapping")
