"""OS-37 N3.  The ownership record, its fences, and the ONE gate that grants permission.

This module performs **no** process action and **no** filesystem action.  It decides
whether an action is permitted; :mod:`standalone_pty`, :mod:`standalone_drivers`,
:mod:`standalone_journal` and :mod:`standalone_adapter` perform them.  Keeping the decision
and the effect in different modules is what makes "nothing happens before ownership is
re-verified" checkable rather than reviewable.

The mechanism is a :class:`Permit`.  Every mutating path takes one, and a ``Permit`` can
only be constructed by :func:`assert_may_act` in this module -- its ``__init__`` refuses a
caller that does not present a module-private token.  A caller therefore cannot reach a
signal, a write, a reuse, a resume, a release or a settle without having gone through the
gate, and a static test asserts that no module outside this one constructs one.  That is
DESIGN D8.2 and the ticket's C8: *"ownership 재검증 전에는 input, signal, reuse, resume 또는
settlement를 수행하지 않는다."*

Three rules travel with the record and are enforced in :func:`verify`:

1. A start that cannot prove process identity **fails**, and a failed start proves its own
   teardown or raises.
2. A missing or untrimmed ``process_incarnation`` is ``unverifiable``, **never** ``exited``.
3. ``spawn_token`` alone authorizes nothing.  It is diagnostic evidence.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from .contracts import parse_host_scope

# ---- the closed action set the gate covers ---------------------------------------------
#: Every effect that requires a re-verified ownership permit.  Closed: a new effect must be
#: added here, which is a visible edit, rather than quietly slipping past the gate.
GATED_ACTIONS = ("signal", "write_input", "reuse_session", "resume_run",
                 "release_terminal", "settle")

#: A FENCE performs zero process and zero filesystem actions and is NEVER upgraded to a
#: stop (`docs/AGENT_EXECUTION_CONTRACT.md:256-259`).  It is listed apart from
#: GATED_ACTIONS on purpose: fencing needs no permit because it does nothing.
FENCE_ACTION = "fence"

#: The seven never-touch obligations, as named refusal codes
#: (`docs/AGENT_EXECUTION_CONTRACT.md:235-259`).  `assert_may_act` refuses anything this
#: runtime did not create for THIS dispatch, and names which obligation refused it.
NEVER_TOUCH_REFUSALS = (
    "not_created_by_this_runtime",   # 1. only what this runtime spawned
    "not_this_dispatch",             # 2. only this dispatch's own resource
    "worktree_resource",             # 3. never a worktree
    "setup_terminal",                # 4. never a setup terminal
    "configured_tab",                # 5. never a configured tab
    "reused_preexisting",            # 6. never a reused or pre-existing terminal
    "user_taken_over",               # 7. never a user-taken-over resource
)

#: Sentinel for an exit status that was never verified.  NOT an exit code: a caller that
#: reads this must report LOST, never `exited{-1}` and certainly never `exited{0}`.
UNVERIFIED_PROCESS_EXIT_CODE = -1

#: Worktree selectors that denote nothing durable.  `active`/`current` are ALIASES the
#: reading process re-resolves, so persisting one persists no worktree at all -- the same
#: rule `orca_adapter.WORKTREE_ALIASES` states, restated here because this module must not
#: import that one (it is the Orca adapter).
UNSTABLE_WORKTREE_SELECTORS = frozenset({"current", "active", ""})
_STABLE_WORKTREE = re.compile(r"^id:[^:]+::.+$")

_UNTRIMMED = re.compile(r"^\s|\s$")


class IdentityError(ValueError):
    """An ownership record is malformed.  Raised at construction."""


class OwnershipRefused(RuntimeError):
    """:func:`assert_may_act` denied an action.

    The caller's correct response is a NAMED refusal -- ``interrupt_outcome="not_owned"``,
    no signal, no write, no settle -- never a retry and never a best-effort attempt.
    """


class StandaloneTeardownUnproven(RuntimeError):
    """A failed start could not prove its own teardown.

    Raised rather than swallowed: `docs/AGENT_EXECUTION_CONTRACT.md:418-424` requires a
    failed start to prove teardown OR raise, and a silent cleanup is neither.
    """


class OwnershipRecord(TypedDict):
    """Every field is REQUIRED.  No field has a default.

    A default here would be a fabricated identity, and a fabricated identity is what lets a
    signal reach a process this runtime never started.
    """

    # -- the six-axis binding the ticket names, in the ticket's order --------------------
    run_id: str
    repo_id: str
    worktree_selector: str      # stable `id:<repo-id>::<path>`, never `current`/`active`
    agent_id: str
    task_id: str
    dispatch_id: str
    # -- process / session identity ------------------------------------------------------
    session_id: str             # durable; survives the minting process
    pid: int
    pgid: int
    sid: int
    captured_tty: str           # PINNED AT SPAWN; re-compared at every signal
    pty_id: str
    process_incarnation: str    # matched as f"{session_id}:{incarnation}"
    # -- scope + evidence ----------------------------------------------------------------
    host_scope: str | None      # `local` for the MVP; UNPARSABLE -> None, never a default
    spawn_token: str            # diagnostic evidence ONLY
    started_at: str
    argv_digest: str
    env_digest: str
    # -- provenance flags the never-touch obligations read -------------------------------
    created_by_this_runtime: bool
    resource_kind: str          # `pty_session`; anything else is refused
    user_taken_over: bool


_REQUIRED_KEYS = frozenset(OwnershipRecord.__annotations__)

# The module-private construction token.  A `Permit` whose `_token` is not this object
# raises.  It is a plain object() rather than a string so it cannot be guessed, typed,
# copied out of a traceback or reconstructed from a serialised form.
_PERMIT_TOKEN = object()


@dataclass(frozen=True)
class Permit:
    """Proof that ownership was re-verified for ONE action on ONE record.

    Only :func:`assert_may_act` can construct one.  It carries the fence value it was
    granted against, so a callee can assert the permit it was handed belongs to the record
    it is about to act on -- a permit for another session is not permission.
    """

    action: str
    fence: str
    session_id: str
    process_incarnation: str
    pid: int
    pgid: int
    captured_tty: str
    verified_at_seq: int
    _token: Any = None

    def __post_init__(self) -> None:
        if self._token is not _PERMIT_TOKEN:
            raise OwnershipRefused(
                "a Permit may only be constructed by standalone_identity.assert_may_act; "
                "an action taken on a forged permit would be an unverified action")

    def covers(self, record: Mapping[str, Any], action: str) -> bool:
        """Whether this permit authorises ``action`` on ``record``."""
        return (self.action == action and self.fence == fence(record)
                and self.pid == record.get("pid"))


# ---- construction ----------------------------------------------------------------------
def mint_session_id(*, run_id: str, dispatch_id: str, task_id: str) -> str:
    """Mint the durable session identity, BEFORE the spawn.

    Minted before the spawn on purpose: it is the value the readiness quorum's R-B compares
    the CLI's own structured record against, by equality.  A value minted *after* observing
    output could be extracted from that output; a value minted microseconds earlier and
    passed on argv cannot be produced by a frame the runtime did not cause.

    **The shape is a bare RFC 4122 UUID, and that is a requirement rather than a taste.**
    DESIGN §D6 (`DESIGN.md:644`) specifies a minted UUID, and it is MEASURED that at least
    one supported agent binary refuses any other shape outright -- it exits with an invalid
    session-id error before emitting anything, which leaves the readiness rehearsal
    unsatisfiable and makes every real dispatch refuse `profile_readiness_unverified`.  A
    prefixed form would therefore be a value no real driver could ever be told.  The
    measurement itself is quoted in the driver layer and in the run's TEST artifact; this
    module names no CLI, which §D4.1 forbids and `test_os37_driver_isolation` enforces.
    """
    return str(uuid.uuid4())


def mint_incarnation() -> str:
    """A fresh process incarnation.  Distinct per spawn attempt, including retries."""
    return f"i-{uuid.uuid4().hex[:16]}"


def mint_spawn_token() -> str:
    """A diagnostic marker only.  Authorizes nothing, ever."""
    return f"t-{uuid.uuid4().hex}"


def stable_worktree_selector(repo_id: str, path: str | os.PathLike[str]) -> str:
    """The one selector shape that denotes the same worktree in every process."""
    return f"id:{repo_id}::{os.path.abspath(os.fspath(path))}"


def argv_digest(argv: Any) -> str:
    """A digest over argv, for detecting that a successor is looking at a different launch."""
    return hashlib.sha256("\x00".join(str(part) for part in argv).encode()).hexdigest()


def make_record(**fields: Any) -> OwnershipRecord:
    """Build and VALIDATE an ownership record, or raise.

    ``host_scope`` is parsed through :func:`contracts.parse_host_scope`, so an unparsable
    scope becomes ``None`` -- a reportable absence -- rather than defaulting to ``local``.
    Defaulting it would let a handle minted for another host be signalled here.
    """
    missing = _REQUIRED_KEYS - set(fields)
    extra = set(fields) - _REQUIRED_KEYS
    if missing or extra:
        raise IdentityError(
            f"ownership record needs exactly {sorted(_REQUIRED_KEYS)!r}; "
            f"missing={sorted(missing)!r} unexpected={sorted(extra)!r}")
    record = dict(fields)
    record["host_scope"] = parse_host_scope(record.get("host_scope"))
    for name in ("run_id", "repo_id", "agent_id", "task_id", "dispatch_id",
                 "session_id", "captured_tty", "pty_id", "process_incarnation",
                 "spawn_token", "started_at", "argv_digest", "env_digest"):
        if not isinstance(record[name], str) or not record[name]:
            raise IdentityError(f"{name} must be a non-empty string")
    for name in ("pid", "pgid", "sid"):
        if not isinstance(record[name], int) or isinstance(record[name], bool):
            raise IdentityError(f"{name} must be an int")
    selector = record["worktree_selector"]
    if selector in UNSTABLE_WORKTREE_SELECTORS or not _STABLE_WORKTREE.match(selector or ""):
        raise IdentityError(
            f"worktree_selector {selector!r} is not a stable 'id:<repo-id>::<path>' "
            "selector; an alias denotes nothing durable")
    if record["resource_kind"] != "pty_session":
        raise IdentityError(
            f"resource_kind {record['resource_kind']!r} is not a pty session this runtime "
            "created; no other resource kind is ownable here")
    return record  # type: ignore[return-value]


def fence(record: Mapping[str, Any]) -> str:
    """The identity fence value: ``session_id:process_incarnation``.

    This module COMPUTES the value; it does not own it.  The authority that stores it is
    the runtime-state receipt's ``external_id``, written under the lease token
    (DESIGN D11.3 condition 1).  A journal record's own copy is compared against the
    ledger's, never trusted in place of it.
    """
    return f"{record['session_id']}:{record['process_incarnation']}"


# ---- verification ----------------------------------------------------------------------
class VerifyResult(TypedDict):
    verdict: str          # `verified` | `not_owned` | `unverifiable`
    reason: str           # a member of NEVER_TOUCH_REFUSALS, or a named probe failure
    evidence: dict[str, Any]


def verify(record: Mapping[str, Any], observed: Mapping[str, Any] | None) -> VerifyResult:
    """Re-verify this record against a freshly observed process-table row.

    ``observed`` is ``None`` when the process table could not be read at all.  That is
    ``unverifiable`` -- **never** ``exited``, and never ``verified``.  Rule 2 of this
    module's docstring is exactly this branch, and it is why the three verdicts are three
    rather than a boolean: "I could not look" and "it is gone" route differently.
    """
    incarnation = record.get("process_incarnation")
    if not isinstance(incarnation, str) or not incarnation \
            or _UNTRIMMED.search(incarnation):
        return {"verdict": "unverifiable",
                "reason": "process_incarnation_missing_or_untrimmed",
                "evidence": {"process_incarnation": incarnation}}
    if observed is None:
        return {"verdict": "unverifiable", "reason": "process_table_unreadable",
                "evidence": {}}
    if not observed:
        # The table was read and this pid is not in it.  That is an absence, and the
        # caller decides what an absence means for the question it asked -- this function
        # does not upgrade it to `exited`, because a reaped pid and a recycled pid look
        # identical from here.
        return {"verdict": "not_owned", "reason": "pid_absent_from_table",
                "evidence": {"pid": record.get("pid")}}
    tty = observed.get("tty")
    if record.get("captured_tty") in ("?", "??", ""):
        return {"verdict": "not_owned", "reason": "unbound_tty",
                "evidence": {"captured_tty": record.get("captured_tty")}}
    if tty != record.get("captured_tty"):
        # A recycled pid: the pid exists but on a different tty than the one pinned at
        # spawn.  Signalling it would signal a stranger.
        return {"verdict": "not_owned", "reason": "captured_tty_mismatch",
                "evidence": {"expected_tty": record.get("captured_tty"), "observed_tty": tty}}
    for axis in ("pgid", "sid"):
        observed_value = observed.get(axis)
        if observed_value is None:
            continue
        if axis == "sid" and not observed_value:
            # AN AXIS THE PLATFORM DID NOT REPORT IS NOT A CONFLICTING AXIS.
            # FACT, measured on this host: darwin's `ps` has no usable session-id keyword --
            # `sid` is refused outright and `sess` answers 0 -- so a strict comparison here
            # would refuse EVERY signal on the MVP's own platform.  A zero therefore means
            # "this axis carries no evidence", exactly as it does for `boot_id` and
            # `proc_start_ticks`, and the axes that ARE reported still have to match.
            #
            # This is a narrower check, not a laxer one: nothing is defaulted, nothing is
            # fabricated, and the axes doing the real work -- the pinned tty and the process
            # group -- are unaffected.  What it does NOT do is pretend to have checked
            # something it could not read.
            continue
        if observed_value != record.get(axis):
            return {"verdict": "not_owned", "reason": f"{axis}_mismatch",
                    "evidence": {f"expected_{axis}": record.get(axis),
                                 f"observed_{axis}": observed_value,
                                 "axis_reported": True}}
    for axis in ("boot_id", "proc_start_ticks"):
        expected = record.get(axis)
        if expected is not None and axis in observed and observed[axis] != expected:
            # A pid recycled across a reboot, or a pid recycled within one.
            return {"verdict": "not_owned", "reason": f"{axis}_mismatch",
                    "evidence": {f"expected_{axis}": expected, f"observed_{axis}": observed[axis]}}
    return {"verdict": "verified", "reason": "", "evidence": dict(observed)}


def reuse_allowed(record: Mapping[str, Any], observed: Mapping[str, Any] | None,
                  *, offered_incarnation: str) -> VerifyResult:
    """Whether this session may be REUSED for ``offered_incarnation``.

    A mismatched incarnation refuses reuse and **no input is written** -- the point of V-12
    case 1.  Reuse is the most dangerous of the gated actions because it is the one that
    looks harmless: the session is alive and the handle resolves, and the only thing wrong
    is that it belongs to a different incarnation.
    """
    if offered_incarnation != record.get("process_incarnation"):
        return {"verdict": "not_owned", "reason": "incarnation_mismatch",
                "evidence": {"offered": offered_incarnation,
                             "recorded": record.get("process_incarnation")}}
    return verify(record, observed)


def release_scope(record: Mapping[str, Any]) -> dict[str, Any]:
    """Exactly what a release is permitted to touch: this pty session, and nothing else.

    Returned as data so a release path cannot widen its own scope: it releases what this
    function names, never a worktree, never a pre-existing resource, never "everything for
    this run".
    """
    return {"resource_kind": "pty_session", "session_id": record["session_id"],
            "process_incarnation": record["process_incarnation"],
            "pty_id": record["pty_id"], "captured_tty": record["captured_tty"],
            "dispatch_id": record["dispatch_id"]}


def fence_only(record: Mapping[str, Any]) -> dict[str, Any]:
    """Compute a fence.  Performs ZERO process and ZERO filesystem actions.

    Deliberately returns no permit and takes no action name: there is no code path from a
    fence to a stop, which is what "never upgraded to a stop" has to mean to be true.
    """
    return {"action": FENCE_ACTION, "fence": fence(record),
            "session_id": record["session_id"], "actions_taken": ()}


# ---- the ONE gate ----------------------------------------------------------------------
def assert_may_act(record: Mapping[str, Any], action: str, *,
                   observed: Mapping[str, Any] | None = None,
                   seq: int = 0) -> Permit:
    """The only function in this runtime that returns permission.  Raises, or grants.

    Called FIRST by every mutating path.  ``observed`` is a freshly read process-table row
    -- freshly, because a snapshot older than the staleness budget is refused upstream by
    :mod:`standalone_pty` rather than served to a later request.

    Refuses, by name, anything this runtime did not create for THIS dispatch: no worktree,
    no setup terminal, no configured tab, no reused or pre-existing terminal, no
    user-taken-over terminal, no unrelated process.
    """
    if action == FENCE_ACTION:
        raise OwnershipRefused(
            "a fence needs no permit because it performs no action; call fence_only(). "
            "Granting a permit for a fence is how a fence becomes a stop")
    if action not in GATED_ACTIONS:
        raise OwnershipRefused(
            f"action {action!r} is not a member of the closed gated set {GATED_ACTIONS!r}")
    if not record.get("created_by_this_runtime"):
        raise OwnershipRefused(f"{NEVER_TOUCH_REFUSALS[0]}: {action} refused")
    if record.get("user_taken_over"):
        raise OwnershipRefused(f"{NEVER_TOUCH_REFUSALS[6]}: {action} refused")
    if record.get("resource_kind") != "pty_session":
        raise OwnershipRefused(
            f"{NEVER_TOUCH_REFUSALS[2]}: {record.get('resource_kind')!r} is not a pty "
            f"session; {action} refused")
    if not record.get("dispatch_id"):
        raise OwnershipRefused(f"{NEVER_TOUCH_REFUSALS[1]}: {action} refused")
    if record.get("host_scope") != "local":
        # An unparsable or non-local scope is refused rather than localised.  This is the
        # branch that keeps `parse_host_scope` returning None from becoming harmless.
        raise OwnershipRefused(
            f"host_scope {record.get('host_scope')!r} is not local; the MVP owns local "
            f"processes only and {action} is refused")
    result = verify(record, observed)
    if result["verdict"] != "verified":
        raise OwnershipRefused(
            f"{result['verdict']}:{result['reason']}: {action} refused; "
            "no signal, no write and no settlement was performed")
    return Permit(action=action, fence=fence(record), session_id=record["session_id"],
                  process_incarnation=record["process_incarnation"], pid=record["pid"],
                  pgid=record["pgid"], captured_tty=record["captured_tty"],
                  verified_at_seq=seq, _token=_PERMIT_TOKEN)


def require_permit(permit: Any, record: Mapping[str, Any], action: str) -> Permit:
    """Assert a callee was handed a real permit that covers THIS record and action.

    Every effecting function calls this on entry.  It closes the gap between "a permit
    exists" and "a permit for this thing exists": a permit granted for another session's
    signal is not permission to write to this one.
    """
    if not isinstance(permit, Permit):
        raise OwnershipRefused(
            f"{action} requires a Permit from standalone_identity.assert_may_act")
    if not permit.covers(record, action):
        raise OwnershipRefused(
            f"the permit presented for {action} was granted for "
            f"{permit.action!r}/{permit.fence!r}, not for {fence(record)!r}")
    return permit


def prove_teardown(*, reaped: bool, esrch: bool, incarnation_absent: bool,
                   detail: str = "") -> None:
    """Assert a failed start proved its own teardown, or RAISE.

    Rule 1.  "Prove teardown" is ``waitpid`` reaped the pid, OR ``kill(pid, 0)`` raised
    ``ESRCH`` after the ladder ran, with the incarnation gone from the table.  Anything
    else is an unproven teardown, and an unproven teardown after a failed start means a
    process this runtime started may still be running unsupervised -- which is not a state
    to return quietly.
    """
    if reaped or (esrch and incarnation_absent):
        return
    raise StandaloneTeardownUnproven(
        "a failed start could not prove its own teardown "
        f"(reaped={reaped} esrch={esrch} incarnation_absent={incarnation_absent})"
        + (f": {detail}" if detail else ""))
