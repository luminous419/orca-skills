"""Executable entry point for the deterministic workflow graph.

This is the runnable half of the engine: it builds state, selects an adapter, invokes or
resumes the compiled graph with an explicit recursion limit, and maps the terminal status
onto a process exit code.  With the fake adapter it runs a complete workflow with no Orca
runtime present; with ``--adapter orca`` it runs the SAME graph against a real Orca Run
through ``OrcaAdapter``, which is the path the bounded OS-42 validation-repair loop has
to be reachable on.

Recursion limit
---------------
LangGraph's default ``recursion_limit`` is 25 steps.  One settled intent costs 5 graph
steps (PREPARE_INTENT, EXECUTE_INTENT, VALIDATE_SETTLEMENT, APPLY_RESULT, ROUTE) and each
phase advance costs 2 (ADVANCE_PHASE, ROUTE), so the canonical 5-phase workflow needs
about 68 steps on its happy path alone and aborts under the default.  Every entry point
here therefore sets the limit explicitly from :func:`default_recursion_limit`.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import recovery_store
from .contracts import BASE_CAPABILITIES
from .executor import IdempotencyRecoveryError, terminal_node
from .runtime_state import (RuntimeStateConflict, resolve_runtime_state,
                            runtime_state_error_code)
from .state import (StateError, initial_state, normalize_malformed_state, typed_update,
                    validate_state)

# Terminal status -> process exit code.  Distinct non-zero codes let a caller tell a
# quality/decision block apart from an exhausted iteration budget.
EXIT_CODES = {"COMPLETED": 0, "BLOCKED": 1, "ESCALATED": 2,
              # OS-31.  A run waiting for a human decision is not a failure and is not an
              # undetermined settlement; it gets its own code, as do the two dispositions.
              "WAITING_FOR_INPUT": 4, "CANCELLED": 5, "ABANDONED": 6}
USAGE_EXIT_CODE = 3

STEPS_PER_INTENT = 5      # PREPARE_INTENT, EXECUTE_INTENT, VALIDATE_SETTLEMENT, APPLY_RESULT, ROUTE
STEPS_PER_ADVANCE = 2     # ADVANCE_PHASE, ROUTE
FIXED_STEPS = 8           # VALIDATE, the entry ROUTE, TERMINAL and END, with slack
RECURSION_SAFETY_MARGIN = 20

CANONICAL_PHASES = ("ANALYSIS", "PLAN", "DESIGN", "IMPLEMENTATION", "TEST")

#: The thread a launch specification that names none is bound to.  Round-7 consolidated
#: review, blocker 1: `build_state` defaulted the state/ledger identity to this value while
#: `build_standalone_adapter` derived the create-once authority from the RAW optional field
#: and recorded ``""`` -- two identities for one launch, so every later resume / recover /
#: watchdog read the durable thread ``"launcher"`` and refused the authority as wrong-thread.
#: There is now ONE resolver, :func:`effective_thread_id`, and every durable record (state,
#: ledger path, authority, prompt composition, migration log) binds its answer.
DEFAULT_THREAD_ID = "launcher"


def effective_thread_id(spec: Mapping[str, Any]) -> str:
    """The thread identity a launch specification EFFECTIVELY names: its ``thread_id`` when
    it carries one, else :data:`DEFAULT_THREAD_ID`.  The one function every launch-time
    writer of a standalone thread identity reads, so the ledger path, the authority, the
    prompt composition and the migration log cannot disagree with the state about which
    thread a run is.  It mirrors the expression `build_state` binds into the state --
    ``spec.get("thread_id", "launcher")``, a line frozen by the D-2(a) byte guard over the
    Orca/fake state builder, which is why that line is not rewritten to call this -- and
    `build_standalone_adapter` asserts the two agree on every composition.  A spec that
    names an EMPTY thread is refused by `state.validate_state` downstream exactly as
    before; this resolver does not repair it."""
    return spec.get("thread_id", DEFAULT_THREAD_ID)

# Where the durable idempotency ledger lives when ``--runtime-state`` is not given.  It is
# a real file, not an in-process store: the whole point is to survive the process, so that
# a restart recovers the receipt instead of creating a second Task/Dispatch.  Operators
# who want a stable, backed-up location set this variable or pass ``--runtime-state``.
RUNTIME_STATE_DIR_ENV = "ORCA_OS40_RUNTIME_STATE_DIR"
RUNTIME_STATE_DIR_NAME = "orca-os40-runtime-state"

# OS-31.  Where the durable OS-40 checkpoint store lives when ``--checkpoint-store`` is not
# given.  The default is the run's own mutable-control area, beside ``.timing_state.json``,
# because that is the one directory a brand-new Coordinator can find from the run id alone.
CHECKPOINT_DIR_ENV = "ORCA_OS40_CHECKPOINT_DIR"
CHECKPOINT_STORE_FILENAME = ".workflow_checkpoints.json"


def default_runtime_state_dir() -> Path:
    override = os.environ.get(RUNTIME_STATE_DIR_ENV)
    return Path(override) if override else Path(tempfile.gettempdir()) / RUNTIME_STATE_DIR_NAME


def default_runtime_state_path(run_id: str, thread_id: str) -> Path:
    """A stable per-run ledger path, so a rerun of the same run recovers its own receipts."""
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in f"{run_id}__{thread_id}")
    return default_runtime_state_dir() / f"{safe}.json"


def resolve_checkpoint_path(run_id: str, thread_id: str, *, explicit: Any = None,
                            artifact_base: Path | None = None) -> Path:
    """The Tier-1 store path, in resolution order: explicit, env override, run artifact root."""
    if explicit:
        return Path(explicit)
    override = os.environ.get(CHECKPOINT_DIR_ENV)
    if override:
        # The suffix keeps the checkpoint store distinct from the runtime-state ledger,
        # which uses the same <run_id>__<thread_id> stem and may share a directory.
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_"
                       for ch in f"{run_id}__{thread_id}")
        return Path(override) / f"{safe}.checkpoints.json"
    base = Path(artifact_base) if artifact_base is not None else Path(".")
    return base / "artifacts" / "runs" / run_id / CHECKPOINT_STORE_FILENAME


class LauncherError(ValueError):
    """The launcher inputs are unusable; no graph run was attempted."""


def default_recursion_limit(state: dict[str, Any]) -> int:
    """Worst-case step budget for this state's phases and iteration budget."""
    phases = max(1, len(state.get("requested_phases") or ()))
    budget = state.get("max_iterations")
    budget = budget if isinstance(budget, int) and budget > 0 else 1
    rounds = (2 * phases + 1) * budget    # worker + reviewer per phase, plus final review
    return (STEPS_PER_INTENT * rounds + STEPS_PER_ADVANCE * rounds
            + FIXED_STEPS + RECURSION_SAFETY_MARGIN)


def build_state(spec: dict[str, Any]) -> dict[str, Any]:
    """Build a validated initial state from a small JSON launch specification.

    ---- OS-37 correction R4: the DECLARED decision block ------------------------------
    ``decision_state`` is optional and defaults to ``initial_state``'s own ``CLEAR``, so a
    specification that does not name one produces byte-for-byte the state it produced
    before.  Naming one matters because ``decision_state`` is written by NO graph node --
    ``state.SET_DECISION`` is its only writer -- so a run launched from this CLI could
    never carry the NEEDS_INPUT/CONFLICT the pause route reads, and the graph's PAUSE node
    was therefore unreachable from the command line for EVERY adapter, not just standalone.
    The approval port alone does not fix that; both halves are the R4 wiring.

    It is a DECLARATION, not an authority, and it buys nothing on its own: PAUSE still
    requires a real ``human_approval`` port, still reconstructs the dispatch set durably,
    and still refuses ``PAUSE_NOT_ADMISSIBLE`` unless the approval authority can produce
    blocked sources that authenticate against this run's real OS-29 ledger.  A declared
    block with nothing behind it therefore reaches BLOCK, exactly as before.

    The value is applied through the engine's OWN ``SET_DECISION`` command rather than
    written into the mapping, so an unknown member is refused by ``state.py``'s rule and
    this function cannot become a second, laxer definition of the decision vocabulary.
    """
    if not isinstance(spec, dict):
        raise LauncherError("state specification must be a JSON object")
    phases = spec.get("phases") or list(CANONICAL_PHASES)
    if not isinstance(phases, list) or not phases:
        raise LauncherError("phases must be a non-empty list")
    capabilities = spec.get("capabilities")
    capabilities = frozenset(capabilities) if capabilities else BASE_CAPABILITIES
    try:
        state = dict(initial_state(
            run_id=spec.get("run_id", "run_launcher"),
            thread_id=spec.get("thread_id", "launcher"),
            phases=tuple(phases), capabilities=capabilities,
            risk=spec.get("risk", "high"), max_iterations=spec.get("max_iterations", 5)))
        declared = spec.get("decision_state")
        if declared is not None:
            state = dict(validate_state(
                {**state, **typed_update(
                    "SET_DECISION", decision_state=declared,
                    decision_reason_code=spec.get("decision_reason_code"))},
                expected_thread_id=state["thread_id"]))
        return state
    except (StateError, TypeError, ValueError, KeyError, IndexError) as exc:
        raise LauncherError(f"invalid state specification: {exc}") from exc


def _standalone_journal_for(artifact_base: Any, run_id: str) -> Any:
    """This run's standalone execution journal.  Adopts nothing and claims nothing."""
    from .standalone_journal import ExecutionJournal
    return ExecutionJournal(artifact_base, run_id)


def _standalone_observation(artifact_base: Any, capabilities: Any,
                            ledger_factory: Any = None) -> Any:
    """The OS-37 standalone ``RunObservationPort``.  Invokes no Orca CLI.

    ``ledger_factory`` (consolidated follow-up review of ``87f6179``, finding 1) lets the
    liveness probe read the receipt fence and the exit sentinel, so an open journal row is
    never mistaken for a live worker; the wiring passes the run's launch-recorded ledger."""
    from .standalone_adapter import StandaloneRunObservation
    return StandaloneRunObservation(
        artifact_base,
        journal_factory=lambda run_id: _standalone_journal_for(artifact_base, run_id),
        capabilities=capabilities, ledger_factory=ledger_factory)


def standalone_profile_path(artifact_base: Any, run_id: str) -> Path:
    """Where a standalone run's DRIVER PROFILE is persisted, beside its journal.

    Consolidated review finding 5.  A stalled standalone run is recovered by a DIFFERENT
    process -- the Watchdog -- which has to rebuild the same runtime the launcher built,
    and the runtime is the profile: binary, driver, selectors, timeouts, worktree.  The
    profile is a committed configuration file that names secrets only by REFERENCE
    (`auth_secret_ref` holds environment names, never values; `driver_env` is non-secret by
    contract), so persisting it under the run root discloses nothing the operator's own
    profile file does not.
    """
    from .standalone_journal import journal_path
    return journal_path(artifact_base, run_id).with_name("profile.json")


def _profile_plain(value: Any) -> Any:
    # A Python caller may hand over `dataclasses.asdict(profile)`, whose `graceful_hint`
    # is bytes; the JSON door re-encodes a str hint with `.encode()`, so the round trip
    # through the persisted file is exact for any UTF-8 hint.
    if isinstance(value, bytes):
        return value.decode("utf-8", "surrogateescape")
    raise TypeError(f"the profile spec is not JSON-shaped: {type(value).__name__}")


def profile_payload(profile_spec: Mapping[str, Any]) -> str:
    """The CANONICAL serialisation of a profile spec.  One function, so the digest a
    launch binds, the archive a recovery reads and the check an override is validated
    against are byte-identical -- content addressing is only sound if every caller
    addresses the same bytes."""
    return json.dumps(dict(profile_spec), sort_keys=True, indent=2, ensure_ascii=False,
                      default=_profile_plain)


def profile_digest(profile_spec: Mapping[str, Any]) -> str:
    """The content address of a profile spec (follow-up review of ``87f6179``, finding
    2).  The run/thread authority binds THIS value, a recovery rebuilds from the archive
    named by it, and an ``--standalone-profile`` override is admitted only when its own
    digest equals it -- so the profile a stalled run is recovered with is the profile it
    was launched with, proven by content and not by a mutable global file."""
    import hashlib
    return hashlib.sha256(profile_payload(profile_spec).encode("utf-8")).hexdigest()[:16]


def profile_archive_path(artifact_base: Any, run_id: str, digest: str) -> Path:
    """``standalone/profiles/<digest>.json`` -- the write-once, content-addressed profile
    a recovery of a given run/thread rebuilds from."""
    return standalone_profile_path(artifact_base, run_id).parent / "profiles" / f"{digest}.json"


def profile_unfrozen_paths(profile_spec: Mapping[str, Any]) -> tuple[str, ...]:
    """The launch-relative directory fields of a profile spec that are still UNFROZEN:
    a ``worktree`` that is RELATIVE **or OMITTED / EMPTY** (round-8 item 3), and each
    relative ``add_dirs[i]``.  Empty means the spec is frozen -- every directory it names
    is absolute and so means the same directory in every process.

    An omitted worktree is unfrozen for the same reason a relative one is: the runtime
    reads it as "this process's cwd" (`StandaloneSession.worktree_path` defaults to
    ``os.getcwd()``), which is the launching process's directory at launch and the
    RECOVERING process's directory on resume / watchdog recovery -- so a run launched in A
    and recovered from B silently sent its next agents to B.  Only an ABSOLUTE worktree
    means one directory everywhere."""
    out: list[str] = []
    worktree = profile_spec.get("worktree", "")
    # Item 4 (round 9): a NON-STRING worktree is not "unfrozen", it is INVALID -- the
    # callers refuse it by its own name (`_refuse_invalid_worktree`) before this question.
    if not (isinstance(worktree, str) and worktree and os.path.isabs(worktree)):
        out.append("worktree")
    add_dirs = profile_spec.get("add_dirs") or ()
    if isinstance(add_dirs, (list, tuple)):
        for index, directory in enumerate(add_dirs):
            if isinstance(directory, str) and directory and not os.path.isabs(directory):
                out.append(f"add_dirs[{index}]")
    return tuple(out)


def freeze_profile_worktree(profile_spec: Mapping[str, Any], *,
                            launch_base: Any = None) -> dict[str, Any]:
    """The profile spec with its launch-relative directories FROZEN to the absolute paths
    the launching process means by them -- Final-Review iteration 5, B1.

    A profile is an operator's file, and a relative ``worktree`` (or ``add_dirs`` entry) in
    it means "relative to where I launch from".  Iteration 4 resolved that at
    `profile_from_mapping`, i.e. at RUNTIME CONSTRUCTION, against whatever process was
    constructing -- but the archive the run/thread authority binds still held the RAW
    relative string, so a Watchdog started from another cwd rebuilt a different worktree
    from byte-identical archived bytes.  The resolution now happens ONCE, here, at the
    launch composition door, BEFORE the spec is digested, exact-match checked, archived
    and handed to the runtime: the archive holds the launch-time absolute path and no
    later process interprets it.

    ``launch_base`` is the directory a relative path is resolved against; ``None`` means
    this process's cwd, which is what the operator's relative path means at launch.  An
    absolute path is returned BYTE-UNCHANGED (no normalisation), so a spec that already
    names absolute directories has the same digest it always had.

    **An OMITTED / EMPTY worktree is frozen to ``launch_base`` itself (round-8 item 3).**
    The runtime reads an empty worktree as "this process's cwd", and the archive is read
    by a DIFFERENT process on resume / watchdog recovery -- so a run launched in A and
    recovered from B rebuilt a runtime whose agents ran in B.  The launch cwd is what the
    operator meant, so that is what is persisted: absolute, bound into the digest, and
    never re-interpreted.  The digest consequences are the same as for a relative path:
    the same profile relaunched from the launch cwd is an exact-match restart, from
    another cwd it is a different worktree and the typed ``STANDALONE_AUTHORITY_CONFLICT``.
    An empty ``add_dirs`` entry is left alone -- it names no directory to freeze.

    **The create-once digest is computed over the FROZEN mapping.**  That is safe -- and
    is the point -- because the digest names the launch conditions the run actually
    executes under: two launches of the same relative spec from the same cwd freeze to the
    same bytes and are an exact-match restart; from DIFFERENT cwds they freeze to
    different bytes, which is a DIFFERENT worktree and therefore refused by name
    (``STANDALONE_AUTHORITY_CONFLICT``) rather than silently re-bound.
    """
    # Item 4 (round 9): refused BEFORE anything is frozen, digested or persisted.  Only a
    # missing key or the exact "" means the launch cwd.
    _refuse_invalid_worktree(profile_spec, where="the profile to freeze")
    frozen = dict(profile_spec)
    base = os.fspath(launch_base) if launch_base is not None else os.getcwd()
    if not os.path.isabs(base):
        base = os.path.abspath(base)

    def _absolute(directory: str) -> str:
        return os.path.normpath(os.path.join(base, directory))
    worktree = frozen.get("worktree", "")
    if worktree == "":
        frozen["worktree"] = base                        # omitted / "": the launch cwd itself
    elif not os.path.isabs(worktree):
        frozen["worktree"] = _absolute(worktree)
    add_dirs = frozen.get("add_dirs")
    if isinstance(add_dirs, (list, tuple)):
        frozen["add_dirs"] = [
            _absolute(d) if isinstance(d, str) and d and not os.path.isabs(d) else d
            for d in add_dirs]
    return frozen


def _refuse_invalid_worktree(profile_spec: Mapping[str, Any], *, where: str) -> None:
    """Item 4 (round 9): the typed ``STANDALONE_PROFILE_WORKTREE_INVALID`` refusal for a
    ``worktree`` that is present and not a string.  ``""`` and a missing key pass (they
    mean the launch cwd); everything else is refused by name, never re-interpreted."""
    if not isinstance(profile_spec, Mapping):
        raise LauncherError(
            f"{STANDALONE_PROFILE_WORKTREE_INVALID}: {where} is not a JSON object")
    if "worktree" in profile_spec and not isinstance(profile_spec["worktree"], str):
        raise LauncherError(
            f"{STANDALONE_PROFILE_WORKTREE_INVALID}: {where} names worktree="
            f"{profile_spec['worktree']!r}, which is not a string; only an omitted key or "
            "the exact empty string means the launch cwd, and no other value is "
            "interpreted (it is refused before the spec is frozen, digested or persisted)")


def _refuse_unfrozen_profile(profile_spec: Mapping[str, Any], *, where: str,
                             remedy: str) -> None:
    """The typed ``STANDALONE_PROFILE_WORKTREE_UNFROZEN`` refusal, raised by the write door
    (an unfrozen spec must never reach an archive) and by the read door (an archive that
    nevertheless holds one -- the pre-fix model's -- must never be interpreted here).
    Item 4 (round 9): a NON-STRING worktree is refused first, by its own name."""
    _refuse_invalid_worktree(profile_spec, where=where)
    unfrozen = profile_unfrozen_paths(profile_spec)
    if unfrozen:
        values = []
        for field in unfrozen:
            if field == "worktree":
                values.append(f"worktree={profile_spec.get('worktree', '<omitted>')!r}")
            else:
                index = int(field[len("add_dirs["):-1])
                values.append(f"{field}={profile_spec['add_dirs'][index]!r}")
        raise LauncherError(
            f"{STANDALONE_PROFILE_WORKTREE_UNFROZEN}: {where} names a RELATIVE or OMITTED "
            f"directory ({', '.join(values)}); such a directory means one thing in the "
            "launching process (its cwd) and another in any recovery process, so it is "
            "refused rather than re-interpreted against this process's cwd "
            f"{os.getcwd()!r}; {remedy}")


def persist_standalone_profile(artifact_base: Any, run_id: str,
                               profile_spec: Mapping[str, Any]) -> Path:
    """Write the profile spec durably (tmp + rename).

    Two files, because a run may be composed more than once: `run_cli` composes ONE
    adapter for ONE profile, but a caller driving the composition root per dispatch (the
    R10 real-agent harness, a mixed Worker/Reviewer CLI pairing) composes several under
    one run id.  Every distinct profile is kept, content-addressed, under
    ``standalone/profiles/<digest>.json`` (write-once), and ``standalone/profile.json``
    names the CURRENT composition -- the one a recovery re-enters with.
    """
    # Iteration 5, B1: the WRITE door.  An archive holds launch-time absolute directories
    # or nothing; a relative one would be re-interpreted by whichever process reads it.
    _refuse_unfrozen_profile(
        profile_spec, where=f"the profile to persist for run {run_id!r}",
        remedy="the launch composition freezes the spec (freeze_profile_worktree) before "
               "it is digested and archived, so this is a caller that bypassed it")
    target = standalone_profile_path(artifact_base, run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = profile_payload(profile_spec)
    digest = profile_digest(profile_spec)
    archive = profile_archive_path(artifact_base, run_id, digest)
    archive.parent.mkdir(parents=True, exist_ok=True)

    def _write(path: Path) -> None:
        _durable_write(path, payload + "\n")
    if not archive.exists():
        _write(archive)
    else:
        # Item 5 (round 9): an archive that ALREADY sits under this digest is not trusted
        # by its filename.  It is loaded through the PRODUCTION loader -- present, hashes
        # to the digest, frozen, a valid profile -- before this write publishes it as the
        # run's current profile or a migration re-binds authority to it.  A corrupt or
        # tampered file there is a typed refusal, never the profile a recovery rebuilds.
        try:
            _validated_profile_archive(artifact_base, run_id, digest)
        except LauncherError as exc:
            raise LauncherError(
                f"{STANDALONE_PROFILE_ARCHIVE_INVALID}: run {run_id!r} already holds a "
                f"profile archive at {archive} for digest {digest!r} that does not "
                f"validate ({exc}); nothing is published or re-bound over it -- repair or "
                "remove the archive and re-issue") from exc
    current = None
    if target.exists():
        try:
            current = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise LauncherError(
                f"{STANDALONE_ADAPTER_REQUIRES_PROFILE}: the persisted profile at {target} "
                f"is unreadable ({exc})") from exc
    if current != payload + "\n":
        _write(target)
    return target


def _durable_write(path: Path, text: str) -> None:
    """``write`` -> ``fsync(file)`` -> ``rename`` -> ``fsync(dir)``.  Round 4, finding 8.

    The directory fsync is the half that was missing: a rename is a directory entry
    change, and until the directory's own metadata reaches stable storage a crash can
    leave the run's effects and journal on disk with NO profile (or no authority record)
    beside them -- exactly the file the Watchdog needs to rebuild the runtime.  The same
    discipline `standalone_pty.write_spawn_record` already applies to the spawn record.
    """
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    _fsync_directory(path.parent)


def _durable_append(path: Path, text: str) -> None:
    """Append one line and fsync it, so an audit trace reaches stable storage before the
    change it records (B4).  Append rather than tmp+rename: the log is a growing history,
    not a single latest value, and a torn tail is a legible partial entry the reader skips
    rather than a lost file."""
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


# ---- round 4, finding 2: the runtime-state AUTHORITY recorded by the original launch ----
STANDALONE_AUTHORITY_SCHEMA = "os37.standalone_authority.v1"

#: Final-Review iteration 3, B2.  The durable log of a LEGACY-authority upgrade: a run
#: launched by the PRE-fix model recorded ``thread_id: ""`` and no prompt-composition
#: digest, while its durable checkpoint/pause evidence names ``"launcher"``.  The new
#: validator refuses that shape, so such a run could not resume or be watchdog-recovered
#: after upgrade.  The read path recognises ONLY that exact shape whose durable evidence
#: PROVES ``launcher`` and atomically upgrades it to the effective identity, recording the
#: act here (idempotent).  Any empty-thread authority whose evidence is absent / unreadable
#: / another thread stays refused -- a blanket acceptance would reopen the foreign-authority
#: bypass.
STANDALONE_AUTHORITY_UPGRADE_SCHEMA = "os37.standalone_authority_upgrade.v1"

#: Follow-up review finding 5.  A run's recorded runtime-state authority is CREATE-ONCE
#: and EXACT-MATCH: a second launch of the same run and thread that names a different
#: ledger (or a different approval authority, finding 8) is refused by this name before
#: any process exists, and the record on disk is not touched.  A migration is an
#: operator's explicit act on the record itself, never a side effect of re-invoking.
STANDALONE_AUTHORITY_CONFLICT = "STANDALONE_AUTHORITY_CONFLICT"
#: Follow-up review finding 8.  A recovery that cannot restore the exact launch-time
#: approval authority binding refuses rather than composing a different one.
STANDALONE_APPROVAL_AUTHORITY_MISMATCH = "STANDALONE_APPROVAL_AUTHORITY_MISMATCH"
#: Follow-up review finding 2.  A run launched standalone is re-entered standalone; any
#: other adapter selection on it is refused by name rather than composed silently.
STANDALONE_RUN_ADAPTER_MISMATCH = "STANDALONE_RUN_ADAPTER_MISMATCH"
#: Follow-up review finding 2 / 9.  An ``--standalone-profile`` on a recovery whose digest
#: does not equal the one the launch bound is refused: no silent override, no unverified
#: substitution -- an exact digest match or an explicit audited migration only.
STANDALONE_PROFILE_DIGEST_MISMATCH = "STANDALONE_PROFILE_DIGEST_MISMATCH"
#: Iteration-2 review finding B4.  A profile migration that names no new profile, no actor
#: or no reason, or whose new profile is identical to the bound one, is refused: a
#: migration is a deliberate, attributable, CHANGING act, not a silent re-bind.
STANDALONE_MIGRATION_REFUSED = "STANDALONE_MIGRATION_REFUSED"
#: Iteration-4 F3.  The authority upgrade audit log has a crash-torn final fragment that
#: could not be removed (the truncating open / ``ftruncate`` failed -- e.g. an owner
#: append-only ``chflags uappnd`` log denies ``O_WRONLY`` while ``O_APPEND`` still works).
#: The heal REFUSES before any append rather than appending a terminal record onto the
#: fragment and forging a permanent newline-terminated corrupt line; the original bytes are
#: unchanged and reads still skip the fragment, so recovery proceeds once the restriction is
#: lifted.
STANDALONE_UPGRADE_LOG_TORN_UNHEALED = "STANDALONE_UPGRADE_LOG_TORN_UNHEALED"
#: Final-Review iteration 5, B1.  A persisted profile whose ``worktree`` (or an
#: ``add_dirs`` entry) is a RELATIVE path is not durable: the launch resolved it against
#: the launch process's cwd, and a recovery started from ANY OTHER cwd would resolve the
#: same archived bytes to a different -- typically nonexistent -- directory.  The launch
#: door now FREEZES the launch-time absolute path into the spec before it is digested,
#: checked and archived (:func:`freeze_profile_worktree`), the write door refuses to
#: archive an unfrozen spec, and the read door refuses to rebuild a runtime from one --
#: a legacy archive written by the pre-fix model is recovered only through the explicit,
#: audited profile migration (``migrate-standalone-profile``) that names the launch-time
#: absolute worktree, never by re-interpreting its bytes against a new process cwd.
STANDALONE_PROFILE_WORKTREE_UNFROZEN = "STANDALONE_PROFILE_WORKTREE_UNFROZEN"
#: Round-9 consolidated review, item 4.  A profile ``worktree`` that is not a string is
#: refused by this name at every door -- the launch freeze, the archive write door, the
#: archive read door and the runtime construction door -- BEFORE the spec is frozen,
#: digested or persisted.  Only a MISSING key or the exact empty string ``""`` means
#: "the launch cwd"; ``123``, ``[]``, ``{}``, ``None`` and every other non-string used to
#: be treated as omitted and silently replaced by the launching process's cwd, so a
#: malformed profile ran its agents in whatever directory the launcher happened to be in.
STANDALONE_PROFILE_WORKTREE_INVALID = "STANDALONE_PROFILE_WORKTREE_INVALID"
#: Round-9 consolidated review, item 5.  A content-addressed profile archive that ALREADY
#: EXISTS under the digest a launch or migration is about to bind is validated -- digest,
#: frozen paths, schema, through the production loader -- before anything publishes or
#: re-binds authority to it.  It used to be skipped by filename alone, so a corrupt or
#: tampered archive at that path became the profile every recovery of the run rebuilt.
STANDALONE_PROFILE_ARCHIVE_INVALID = "STANDALONE_PROFILE_ARCHIVE_INVALID"
#: The durable audit-log schema for a profile migration.
STANDALONE_MIGRATION_SCHEMA = "os37.standalone_profile_migration.v2"
#: Round-7 consolidated review, blocker 3.  The run's DURABLE THREAD EVIDENCE (its pause
#: record, else its committed checkpoint head) could not be READ: an unreadable or corrupt
#: pause store / checkpoint is not an absence, and a recovery that cannot cross-check the
#: authority's immutable thread binding against it is refused by this name -- on resume,
#: recover, cancel, abandon and watchdog alike -- never composed from a record it could not
#: verify.
STANDALONE_THREAD_EVIDENCE_UNREADABLE = "STANDALONE_THREAD_EVIDENCE_UNREADABLE"
#: Blocker 3, the other non-present state.  The run PROVABLY has no durable thread
#: evidence (a readable pause store holding no record, a readable checkpoint store holding
#: no head), so an authority record that exists for it cannot have its thread binding
#: validated and no route may skip that check because the requested thread was empty.
STANDALONE_THREAD_EVIDENCE_ABSENT = "STANDALONE_THREAD_EVIDENCE_ABSENT"
#: Round-7 consolidated review, blocker 2.  The non-secret PROMPT COMPOSITION inputs a
#: launch persisted (objective, requested phases, risk, project root, role instructions)
#: cannot be rebuilt for a recovery -- the archive the authority binds is missing, does not
#: hash to the bound digest, or is not a composition record -- so the recovery is refused
#: by this name rather than dispatching the next Worker / Reviewer with raw intent JSON.
STANDALONE_PROMPT_COMPOSITION_MISSING = "STANDALONE_PROMPT_COMPOSITION_MISSING"
#: The durable schema of a persisted prompt composition (blocker 2).
STANDALONE_PROMPT_COMPOSITION_SCHEMA = "os37.standalone_prompt_composition.v1"
#: The two composers a launch can declare: the production renderer built from the
#: persisted inputs, or NONE -- a launch that named no objective and therefore delivers the
#: canonical intent payload.  ``none`` is the launch's OWN declaration, recorded at launch;
#: it is never what a recovery falls back to when the record is missing.
PROMPT_COMPOSER_PRODUCTION = "production"
PROMPT_COMPOSER_NONE = "none"
#: Blocker 3's tri-state for :func:`durable_thread_evidence`.
THREAD_EVIDENCE_PRESENT = "present"
THREAD_EVIDENCE_PROVEN_ABSENT = "proven_absent"
THREAD_EVIDENCE_UNREADABLE = "unreadable"
#: The three states a migration record carries (F-001).  A migration is durable only when a
#: ``committed`` record exists for its ``migration_id``; a ``prepared`` with no ``committed``
#: is an interrupted attempt that recovery finishes (``committed``) or voids (``rolled_back``).
MIGRATION_PREPARED = "prepared"
MIGRATION_COMMITTED = "committed"
MIGRATION_ROLLED_BACK = "rolled_back"
#: Round-8 item 8.  Reconciliation of an interrupted migration could not be decided from
#: valid durable state: the authority names neither the attempt's source nor its target
#: digest, the target's profile archive is missing / corrupt / not a profile, or the
#: authority record is absent.  Nothing is appended; the attempt stays open and every
#: authority read refuses by this name until the durable state is repaired.
STANDALONE_MIGRATION_UNRECONCILABLE = "STANDALONE_MIGRATION_UNRECONCILABLE"


def _safe_stem(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)


def standalone_authority_path(artifact_base: Any, run_id: str, thread_id: str = "",
                              *, for_write: bool = False) -> Path:
    """Where a standalone run records WHICH runtime-state ledger it was launched against.

    Beside the persisted profile: `--runtime-state` names an operator-chosen ledger, and a
    recovery that reconstructs the DEFAULT path instead reopens a different (empty)
    authority, reads every claim as `CREATED`, re-executes an already-settled intent and
    then fails `SETTLEMENT_IDENTITY_MISMATCH` against the real one.

    ``runtime_state.json`` is the run's PRIMARY binding -- the first thread launched.  A
    further thread of the same run id (the fixtures drive several through one room)
    records its own ``runtime_state.<thread>.json``, so exact-match is per thread and one
    thread's binding never overwrites another's.  ``thread_id=""`` names the primary.

    Iteration-2 review finding B1.  The READ resolution NEVER lets an existing record
    redirect verification around itself: a request for thread ``t`` resolves to the
    per-thread file ONLY when that file EXISTS, and otherwise to the primary -- so a
    primary whose ``thread_id`` was TAMPERED to name another thread can no longer bounce
    the lookup to a missing per-thread file and make ``load_standalone_authority`` answer
    ``None`` (which then let a foreign adapter in).  The primary is instead read AS this
    thread's authority and refused by :func:`_validate_authority_record` when its recorded
    thread does not match.  ``for_write=True`` keeps the old content-based routing -- a
    SECOND thread's create must land in its own file rather than clobber the primary -- and
    that read of the primary's thread is safe because the writer is recording its own
    record under the create-once guard, not verifying an attacker-supplied one.
    """
    from .standalone_journal import journal_path
    directory = journal_path(artifact_base, run_id).parent
    primary = directory / "runtime_state.json"
    if not thread_id:
        return primary
    per_thread = directory / f"runtime_state.{_safe_stem(thread_id)}.json"
    if per_thread.exists():
        return per_thread
    if for_write and primary.exists():
        try:
            held = json.loads(primary.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            held = None
        # A create for a DIFFERENT thread than the primary's own routes to its own file,
        # so it never overwrites the primary.  An unreadable primary is left to the
        # create-once check, which fails closed on it.
        if isinstance(held, dict) and held.get("thread_id") not in ("", thread_id):
            return per_thread
    return primary


def approval_authority_name(approval_port: Any) -> str:
    """The NAME of a configured approval authority, for the durable binding (finding 8).

    ``none`` for no port, ``artifact`` for the real OS-30 artifact port, and a value
    outside `APPROVAL_AUTHORITIES` for anything else -- which a recovery then refuses,
    because a binding it cannot rebuild by name is a binding it cannot restore exactly.
    """
    if approval_port is None:
        return NO_APPROVAL_AUTHORITY                    # "none"; defined below
    try:
        from scripts.clarification_protocol import ArtifactHumanApprovalPort
    except ImportError:  # installed Skill layout exposes sibling tools directly
        from clarification_protocol import ArtifactHumanApprovalPort  # type: ignore
    if isinstance(approval_port, ArtifactHumanApprovalPort):
        return ARTIFACT_APPROVAL_AUTHORITY
    return f"unrecoverable:{type(approval_port).__name__}"


def _authority_record(run_id: str, *, runtime_state_path: Any, thread_id: str,
                      approval_authority: str, profile_digest: str,
                      prompt_composition_digest: str = "") -> dict[str, Any]:
    return {"schema": STANDALONE_AUTHORITY_SCHEMA, "run_id": run_id,
            "adapter": STANDALONE_ADAPTER,
            "runtime_state_path": str(Path(runtime_state_path).resolve()),
            "thread_id": thread_id, "approval_authority": approval_authority,
            # Follow-up review of `87f6179`, finding 2.  The CONTENT ADDRESS of the
            # profile this thread launched, bound into the closed record so a recovery
            # rebuilds the runtime from `profiles/<digest>.json` -- the thread's OWN
            # profile -- and never from the mutable, run-global `profile.json` that a
            # second thread of the same run overwrites.
            "profile_digest": profile_digest,
            # Round-7 blocker 2.  The content address of the PROMPT COMPOSITION this
            # thread launched with (`prompt_compositions/<digest>.json`), bound the same
            # way, so a recovery rebuilds the SAME production prompt renderer -- or
            # refuses by name -- and never dispatches raw intent JSON.
            "prompt_composition_digest": prompt_composition_digest}


def persist_standalone_authority(artifact_base: Any, run_id: str, *,
                                 runtime_state_path: Any, thread_id: str = "",
                                 approval_authority: str = "none",
                                 profile_digest: str = "",
                                 prompt_composition_digest: str = "") -> Path:
    """Record the launch bindings durably, CREATE-ONCE and EXACT-MATCH.  Finding 5 / 8 / 2.

    The record names the ledger, the thread, the approval authority and the PROFILE
    DIGEST.  A record that already exists for this run and thread must EQUAL the one this
    launch would write; a different one -- a different ledger, a different approval
    authority, or a DIFFERENT PROFILE -- is `STANDALONE_AUTHORITY_CONFLICT`, raised before
    any process exists with the file untouched, so a re-invocation can never redirect the
    recovery of a run it did not launch.  An identical record is a restart and writes
    nothing.
    """
    target = standalone_authority_path(artifact_base, run_id, thread_id, for_write=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    if check_standalone_authority(artifact_base, run_id, runtime_state_path=runtime_state_path,
                                  thread_id=thread_id,
                                  approval_authority=approval_authority,
                                  profile_digest=profile_digest,
                                  prompt_composition_digest=prompt_composition_digest
                                  ) is not None:
        return target                                    # identical: a restart, no write
    wanted = _authority_record(run_id, runtime_state_path=runtime_state_path,
                               thread_id=thread_id, approval_authority=approval_authority,
                               profile_digest=profile_digest,
                               prompt_composition_digest=prompt_composition_digest)
    _durable_write(target, json.dumps(wanted, sort_keys=True, indent=2) + "\n")
    return target


def _validate_authority_record(record: Any, target: Any, run_id: str,
                               requested_thread: str) -> dict[str, Any]:
    """The CLOSED-record validation every read shares (finding 3 / B1).  A record that is
    not a full, coherent standalone launch binding for THIS run and THIS thread is a
    refusal, never treated as an absence: an existing-but-invalid authority must refuse
    every recovery rather than fall through to a foreign composition.

    ``requested_thread`` is the IMMUTABLE thread binding (B1) and it is REQUIRED.  The
    record's own ``thread_id`` MUST equal it -- so a primary record whose ``thread_id`` was
    tampered to name another thread is refused when read as this thread's authority rather
    than silently bouncing the lookup to a missing per-thread file.

    Round-7 consolidated review, blocker 3.  The previous shape compared the thread ONLY
    when ``requested_thread`` was truthy, and `durable_thread_evidence` answered ``""`` for
    an UNREADABLE pause store or checkpoint as well as for a genuine absence -- so corrupt
    durable state disabled the thread fence and a primary authority carrying another
    thread was accepted on the recover/watchdog route.  There is no truthiness guard any
    more: an empty ``requested_thread`` is itself refused here, by name, and the caller
    (:func:`_load_authority_validated`) resolves its thread from the tri-state evidence
    BEFORE this function and refuses ``unreadable`` and ``proven_absent`` by their own
    names.  Nothing reaches the comparison below without a thread to compare against.
    """
    if not requested_thread:
        raise LauncherError(
            f"{STANDALONE_THREAD_EVIDENCE_ABSENT}: the persisted runtime-state authority at "
            f"{target} for run {run_id!r} cannot be validated because no thread identity "
            "was resolved to check its immutable thread binding against; recovery is "
            "refused rather than composed from a record whose thread was not verified")
    if (not isinstance(record, dict)
            or record.get("schema") != STANDALONE_AUTHORITY_SCHEMA
            or record.get("adapter") != STANDALONE_ADAPTER
            or record.get("run_id") != run_id
            or not isinstance(record.get("runtime_state_path"), str)
            or not record["runtime_state_path"]
            or record.get("approval_authority") in (None, "")
            or not isinstance(record.get("profile_digest"), str)
            or not record["profile_digest"]
            or not isinstance(record.get("prompt_composition_digest"), str)
            or not record["prompt_composition_digest"]
            or not isinstance(record.get("thread_id"), str)
            or not record["thread_id"]
            or record["thread_id"] != requested_thread):
        raise LauncherError(
            f"{STANDALONE_ADAPTER_REQUIRES_LEDGER}: the persisted runtime-state authority "
            f"at {target} is not a complete standalone launch binding for run {run_id!r} "
            f"thread {requested_thread!r} (ledger / approval authority / profile digest / "
            "prompt composition digest / thread); recovery is refused rather than composed "
            "from an incomplete, foreign or wrong-thread record")
    return record


class DurableThreadEvidence(dict):
    """Blocker 3's TRI-STATE answer to "which thread did this run launch?".

    ``kind`` is one of :data:`THREAD_EVIDENCE_PRESENT` (``thread_id`` names it and
    ``source`` says which durable record did), :data:`THREAD_EVIDENCE_PROVEN_ABSENT` (the
    pause store and the checkpoint store were BOTH READ and neither holds a record for the
    run) or :data:`THREAD_EVIDENCE_UNREADABLE` (a read FAILED -- ``source`` names the
    store and ``detail`` the failure).  A mapping rather than a string so that an
    unreadable store can never be spelled the same as an absent one.
    """

    @property
    def kind(self) -> str:
        return str(self["kind"])

    @property
    def thread_id(self) -> str:
        return str(self.get("thread_id") or "")

    @property
    def present(self) -> bool:
        return self.kind == THREAD_EVIDENCE_PRESENT


def durable_thread_evidence(artifact_base: Any, run_id: str) -> DurableThreadEvidence:
    """The thread the run ACTUALLY launched, read from its own durable records.  B1'.

    Every recovery route -- resume / cancel / abandon / recover / watchdog -- must validate
    the authority's immutable thread binding against this, never against a caller-supplied
    empty string.  The order mirrors the Watchdog wiring's own `bindings_for`: the pause
    record's thread if the run is paused, else the committed checkpoint head's thread.

    Round-7 blocker 3: the answer is a :class:`DurableThreadEvidence` TRI-STATE, never a
    bare string.  A pause store or checkpoint that cannot be read is reported as
    ``unreadable`` with the failing store named -- it used to be swallowed into ``""``,
    the same spelling as a genuine absence, which let corrupt durable state disable the
    thread fence.  ``proven_absent`` is reported only when BOTH stores were read
    successfully and neither names a thread.
    """
    base = Path(artifact_base)
    try:
        from . import pause_store
        record = pause_store.store_for(run_id, artifact_base=base).read(run_id)
    except Exception as exc:  # noqa: BLE001 - an unreadable pause store is UNREADABLE evidence
        return DurableThreadEvidence(kind=THREAD_EVIDENCE_UNREADABLE, thread_id="",
                                     source="pause_store",
                                     detail=f"{type(exc).__name__}: {exc}")
    thread = str((record or {}).get("thread_id") or "")
    if thread:
        return DurableThreadEvidence(kind=THREAD_EVIDENCE_PRESENT, thread_id=thread,
                                     source="pause_record", detail="")
    try:
        from . import recovery_runtime
        head = recovery_runtime.resolve_head(run_id, artifact_base=base)
    except Exception as exc:  # noqa: BLE001 - an unreadable checkpoint is UNREADABLE evidence
        return DurableThreadEvidence(kind=THREAD_EVIDENCE_UNREADABLE, thread_id="",
                                     source="checkpoint_store",
                                     detail=f"{type(exc).__name__}: {exc}")
    thread = str(getattr(head, "thread_id", "") or "")
    if thread:
        return DurableThreadEvidence(kind=THREAD_EVIDENCE_PRESENT, thread_id=thread,
                                     source="checkpoint_head", detail="")
    return DurableThreadEvidence(kind=THREAD_EVIDENCE_PROVEN_ABSENT, thread_id="",
                                 source="pause_store+checkpoint_store", detail="")


def resolve_recovery_thread(artifact_base: Any, run_id: str, thread_id: str = "") -> str:
    """The thread a recovery route validates against: the caller's own when it names one,
    else the run's durable evidence -- with ``unreadable`` and ``proven_absent`` each
    refused BY NAME (blocker 3) rather than collapsed into an empty string."""
    if thread_id:
        return thread_id
    evidence = durable_thread_evidence(artifact_base, run_id)
    if evidence.kind == THREAD_EVIDENCE_UNREADABLE:
        raise LauncherError(
            f"{STANDALONE_THREAD_EVIDENCE_UNREADABLE}: run {run_id!r}'s durable thread "
            f"evidence could not be read from its {evidence['source']} "
            f"({evidence['detail']}); an unreadable authority is not an absent one, and "
            "recovery is refused rather than composed without a verified thread binding")
    if evidence.kind != THREAD_EVIDENCE_PRESENT:
        raise LauncherError(
            f"{STANDALONE_THREAD_EVIDENCE_ABSENT}: run {run_id!r} has no durable thread "
            "evidence (its pause store holds no record and its checkpoint store holds no "
            "committed head), so the recorded launch authority's thread binding cannot be "
            "validated and there is nothing to recover; recovery is refused rather than "
            "composed from an unverified record")
    return evidence.thread_id


def _load_authority_validated(artifact_base: Any, run_id: str,
                              thread_id: str = "") -> dict[str, Any] | None:
    """Read + validate the authority record.  No reconciliation (avoids recursion with
    :func:`reconcile_standalone_migrations`, which itself reads the authority)."""
    target = standalone_authority_path(artifact_base, run_id, thread_id)
    if not target.exists():
        return None
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LauncherError(
            f"{STANDALONE_ADAPTER_REQUIRES_LEDGER}: the persisted runtime-state authority "
            f"at {target} is unreadable ({exc})") from exc
    effective_thread = resolve_recovery_thread(artifact_base, run_id, thread_id)
    return _validate_authority_record(record, target, run_id, effective_thread)


def load_standalone_authority(artifact_base: Any, run_id: str,
                              thread_id: str = "") -> dict[str, Any] | None:
    """The recorded authority, or ``None`` when the run recorded none.  Unreadable or
    INVALID (including a WRONG-THREAD record, B1/B1') RAISES -- an existing-but-broken
    record is a refusal, not an absence (finding 3).

    B1'.  When the caller names no thread (``thread_id=""`` -- the recover/watchdog route,
    which has no ``--thread-id``), the immutable thread binding is validated against the
    run's DURABLE thread evidence (:func:`durable_thread_evidence`) rather than accepted as
    "any".  So a tampered primary authority is refused with the typed authority error at
    the authority boundary, BEFORE any downstream ``RECOVERY_*`` code, on every route.

    F-001.  The authority READ PATH reconciles an interrupted migration first: a
    ``prepared``-without-``committed`` migration is deterministically finished or rolled
    back (:func:`reconcile_standalone_migrations`) so the digest a reader sees is the
    committed one, never a half-applied one.  A run with no migration log takes a fast
    no-op path, so ordinary loads are unaffected.

    Final-Review iteration 3, B2.  The read path ALSO upgrades a genuine LEGACY authority
    -- ``thread_id: ""`` with no prompt-composition digest -- to the effective identity
    when (and ONLY when) the run's durable evidence PROVES ``launcher``, so an interrupted
    run launched at the PR base can resume and be watchdog-recovered after upgrade.  An
    empty-thread authority whose evidence is absent / unreadable / another thread is
    untouched here and stays refused by :func:`_validate_authority_record`."""
    reconcile_standalone_migrations(artifact_base, run_id, thread_id)
    # Round-9 item 2: an interrupted prompt-composition upgrade is finished or voided
    # BEFORE the legacy check below -- a crash after the rebind left the authority in the
    # upgraded shape, which the legacy fast path no longer recognises.
    reconcile_authority_upgrades(artifact_base, run_id)
    _upgrade_legacy_authority_if_needed(artifact_base, run_id, thread_id)
    return _load_authority_validated(artifact_base, run_id, thread_id)


def _is_legacy_omitted_thread_authority(record: Any, run_id: str) -> bool:
    """Exactly the shape the PRE-fix model wrote for a launch that omitted ``thread_id``:
    a complete standalone binding whose recorded thread is the EMPTY string and which
    carries no prompt-composition digest.  Everything else (a non-empty wrong thread, a
    missing ledger, a foreign run id) is NOT this shape and is left to the normal
    validator -- so this recognises the compatibility case without widening into the
    foreign-authority bypass the empty-thread guard exists to close."""
    return (isinstance(record, dict)
            and record.get("schema") == STANDALONE_AUTHORITY_SCHEMA
            and record.get("adapter") == STANDALONE_ADAPTER
            and record.get("run_id") == run_id
            and isinstance(record.get("runtime_state_path"), str)
            and bool(record.get("runtime_state_path"))
            and record.get("approval_authority") not in (None, "")
            and isinstance(record.get("profile_digest"), str)
            and bool(record.get("profile_digest"))
            and record.get("thread_id") == ""
            and not record.get("prompt_composition_digest"))


def _authority_upgrade_log_path(artifact_base: Any, run_id: str) -> Path:
    """The durable, append-only audit log of legacy-authority upgrades for a run."""
    return standalone_profile_path(artifact_base, run_id).with_name(
        "authority_upgrades.ndjson")


def read_authority_upgrade_records(artifact_base: Any, run_id: str
                                   ) -> tuple[dict[str, Any], ...]:
    """EVERY COMPLETE record of the legacy-authority upgrade log, oldest first --
    ``prepared``, ``committed`` and ``rolled_back`` alike (round-9 item 2), plus any
    state-less record the pre-fix single-write model appended (read as committed).

    Round-10 item 3.  ``_durable_append`` writes one newline-terminated line per record
    and its own contract says a crash-torn final fragment is a legible partial entry the
    reader SKIPS -- but this reader parsed every fragment and RAISED on the torn tail, so a
    single interrupted append (a crash between the write and its own ``\\n``) turned every
    later authority read into a permanent ``STANDALONE_MIGRATION_REFUSED`` that
    reconciliation, watchdog recovery and explicit recovery could never repair.  A record
    is COMPLETE only when it is newline-terminated; the ONLY fragment ever tolerated is a
    non-empty final line with no trailing ``\\n`` (the torn tail), which is quarantined and
    NAMED in the evidence, never parsed.  A malformed but newline-TERMINATED record still
    RAISES -- a complete line that does not parse is corruption, not a torn tail -- and so
    does an unreadable log."""
    path = _authority_upgrade_log_path(artifact_base, run_id)
    if not path.exists():
        return ()
    from .standalone_capture import protocol_lines
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LauncherError(
            f"{STANDALONE_MIGRATION_REFUSED}: the authority upgrade audit log at {path} is "
            f"unreadable ({exc})") from exc
    lines = protocol_lines(text)
    # A trailing delimiter yields no empty last piece (``protocol_lines`` pops it), so a
    # text that does NOT end in ``\n`` has an unterminated final fragment as its last
    # piece: the crash-torn tail.  Quarantine exactly that one, and nothing else.
    if text and not text.endswith("\n") and lines:
        torn = lines.pop()
        _quarantine_torn_upgrade_tail(path, torn)
    out: list[dict[str, Any]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except ValueError as exc:
            raise LauncherError(
                f"{STANDALONE_MIGRATION_REFUSED}: the authority upgrade audit log at {path} "
                f"has an unparsable COMPLETE (newline-terminated) entry ({exc}); a torn "
                "final fragment is skipped, but a complete corrupt record is not") from exc
        if isinstance(record, dict):
            out.append(record)
    return tuple(out)


def _quarantine_torn_upgrade_tail(path: Path, fragment: str) -> None:
    """Record the crash-torn final fragment of an upgrade log beside it, best-effort, so
    the skipped bytes are legible EVIDENCE rather than silently dropped (round-10 item 3).
    A read must never fail because the quarantine could not be written, and re-reading the
    same torn tail overwrites the same file with the same content (idempotent)."""
    try:
        quarantine = path.with_name(path.name + ".torn")
        quarantine.write_text(fragment, encoding="utf-8")
    except OSError:
        pass


def _heal_torn_upgrade_tail_locked(path: Path) -> None:
    """Iteration-3 F2 / iteration-4 F3.  REMOVE a crash-torn final fragment from the upgrade
    log so the next append lands on a clean record boundary -- called under the run's
    migration lock BEFORE any append.

    The read path (:func:`read_authority_upgrade_records`) only *skips* an unterminated
    final fragment in memory; the bytes stay in the file, so the next reconciliation /
    recovery append would concatenate its terminal record directly onto the fragment and
    forge a newline-TERMINATED corrupt line that every later read then refuses forever
    (reviewer `torn_replay.txt`).  Under the lock, this quarantines that fragment to
    ``.torn`` (evidence, idempotent) and TRUNCATES the log to its last newline, fsynced, so
    the fragment can never be a prefix of a future record.  A newline-terminated (COMPLETE)
    log -- including one whose last complete record is corrupt -- is left byte-for-byte
    untouched: a complete corrupt record is not a torn tail and must keep RAISING on read.

    **Iteration-4 F3.**  If the fragment CANNOT be removed -- the truncating open or the
    ``ftruncate``/``fsync`` fails (an owner append-only ``chflags uappnd`` log denies
    ``O_WRONLY`` while ``O_APPEND`` still works, so the healer used to silently return and the
    caller then appended a corrupting terminal record) -- this raises a TYPED refusal
    (:data:`STANDALONE_UPGRADE_LOG_TORN_UNHEALED`) BEFORE any append, leaving the original log
    bytes byte-for-byte unchanged.  Reads still skip the fragment in memory, so once the
    restriction is lifted the heal succeeds and replay converges; nothing is ever appended
    onto an unhealed torn tail."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return
    except OSError:
        return          # unreadable: leave it; the validated read surfaces it by name
    if not data or data.endswith(b"\n"):
        return          # empty or complete -- no torn tail to remove
    keep = data.rfind(b"\n") + 1          # bytes up to and including the last newline (0 if none)
    _quarantine_torn_upgrade_tail(path, data[keep:].decode("utf-8", "replace"))
    try:
        fd = os.open(str(path), os.O_WRONLY)
    except OSError as exc:
        raise LauncherError(
            f"{STANDALONE_UPGRADE_LOG_TORN_UNHEALED}: the authority upgrade audit log at "
            f"{path} has a crash-torn final fragment that could not be removed ({exc}); no "
            "record is appended onto it (that would forge a newline-terminated corrupt line), "
            "the original log bytes are unchanged, and reads still skip the fragment in memory "
            "-- lift the restriction (e.g. `chflags nouappnd`) and the heal and replay proceed"
        ) from exc
    try:
        os.ftruncate(fd, keep)
        os.fsync(fd)
    except OSError as exc:
        raise LauncherError(
            f"{STANDALONE_UPGRADE_LOG_TORN_UNHEALED}: the authority upgrade audit log at "
            f"{path} has a crash-torn final fragment that could not be truncated ({exc}); no "
            "record is appended onto it, the original log bytes are unchanged, and reads still "
            "skip the fragment in memory") from exc
    finally:
        os.close(fd)
    _fsync_directory(path.parent)


def read_authority_upgrades(artifact_base: Any, run_id: str) -> tuple[dict[str, Any], ...]:
    """The COMMITTED legacy-authority upgrades for this run, oldest first -- the truthful
    history of what the authority was re-bound to.  A ``prepared`` with no ``committed``
    is an interrupted attempt, not an upgrade (round-9 item 2); a state-less record from
    the pre-fix single-write model is a committed one."""
    return tuple(r for r in read_authority_upgrade_records(artifact_base, run_id)
                 if r.get("state", MIGRATION_COMMITTED) == MIGRATION_COMMITTED)


def _upgrade_id(run_id: str, digest: str, composition_source: str, actor: str,
                reason: str) -> str:
    """The attributable identity of ONE legacy-authority upgrade operation: the run, the
    composition digest it binds and who / why / how.  A crash retry of the same operation
    recomputes the same id; its tries are told apart by ``attempt``."""
    import hashlib
    payload = json.dumps({"run_id": run_id, "bound_prompt_composition_digest": digest,
                          "composition_source": composition_source, "actor": str(actor),
                          "reason": str(reason)}, sort_keys=True)
    return "up-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _upgrade_attempt_states(artifact_base: Any, run_id: str
                            ) -> dict[tuple[str, int], dict[str, dict[str, Any]]]:
    """``(upgrade_id, attempt) -> {state -> record}`` in log order.  A state-less
    pre-fix record is a committed attempt 1 of an id derived from its own fields."""
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for rec in read_authority_upgrade_records(artifact_base, run_id):
        uid = str(rec.get("upgrade_id") or _upgrade_id(
            run_id, str(rec.get("bound_prompt_composition_digest") or ""),
            str(rec.get("composition_source") or ""), str(rec.get("actor") or ""),
            str(rec.get("reason") or "")))
        attempt = rec.get("attempt")
        attempt = attempt if isinstance(attempt, int) and attempt >= 1 else 1
        state = str(rec.get("state") or MIGRATION_COMMITTED)
        grouped.setdefault((uid, attempt), {})[state] = rec
    return grouped


def _is_effective_authority_bound_to(record: Any, run_id: str, digest: str) -> bool:
    """The exact shape a completed upgrade leaves: the legacy binding rewritten as the
    effective identity (``launcher``) bound to ``digest``."""
    return (isinstance(record, dict)
            and record.get("schema") == STANDALONE_AUTHORITY_SCHEMA
            and record.get("run_id") == run_id
            and record.get("thread_id") == DEFAULT_THREAD_ID
            and record.get("prompt_composition_digest") == digest)


def _reconcile_authority_upgrades_locked(artifact_base: Any, run_id: str) -> None:
    """Round-9 item 2: finish or void every interrupted legacy-authority upgrade,
    deterministically, from the PRIMARY authority file alone.  The caller HOLDS the lock.

    Per attempt with a ``prepared`` and no terminal record:

    * the primary authority is the effective identity bound to the attempt's digest and
      the composition archive validates through the production loader -> the re-bind
      landed: append ``committed`` (ROLL FORWARD);
    * the primary authority is still the exact legacy omitted-thread shape AND the
      attempt's composition was already PUBLISHED (its content-addressed archive validates)
      -> the operator's intent is fully durable but the rebind did not land: COMPLETE it
      here -- atomically re-bind the primary to the attempt's digest and append
      ``committed`` PRESERVING the prepared attempt's own identity (its ``actor`` /
      ``reason`` / ``composition_source``) -- so the anonymous automatic upgrade never
      consumes a published composition that belongs to an open EXPLICIT attempt and drops
      its actor/reason (iteration-3 F3);
    * the primary authority is still the legacy shape and the composition was NOT published
      (crash before the two-phase writer's persist step) -> the re-bind never landed:
      append ``rolled_back`` (ROLL BACK; the next read re-runs the upgrade as a fresh
      attempt);
    * anything else (unreadable, another digest, another thread) is
      :data:`STANDALONE_MIGRATION_UNRECONCILABLE` -- nothing appended, the read refuses.

    Replaying the read after ANY crash cut point therefore converges to exactly one
    ``committed`` record for the live authority, with the ORIGINAL actor/reason of an
    explicit attempt preserved, and a rebind is never left without an audit record: the
    ``prepared`` intent precedes it on stable storage and this closes it.
    """
    log = _authority_upgrade_log_path(artifact_base, run_id)
    _heal_torn_upgrade_tail_locked(log)                  # F2: clean boundary before appends
    primary = standalone_authority_path(artifact_base, run_id, "")
    for (uid, attempt), by_state in _upgrade_attempt_states(artifact_base, run_id).items():
        if MIGRATION_PREPARED not in by_state:
            continue
        if MIGRATION_COMMITTED in by_state or MIGRATION_ROLLED_BACK in by_state:
            continue
        prepared = by_state[MIGRATION_PREPARED]
        digest = str(prepared.get("bound_prompt_composition_digest") or "")
        try:
            current = json.loads(primary.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LauncherError(
                f"{STANDALONE_MIGRATION_UNRECONCILABLE}: run {run_id!r} has an interrupted "
                f"prompt-composition upgrade {uid!r} attempt {attempt} and its primary "
                f"authority at {primary} cannot be read ({exc}); the attempt stays open") from exc
        terminal = dict(prepared)
        terminal["attempt"] = attempt
        terminal["reconciled"] = True
        if digest and _is_effective_authority_bound_to(current, run_id, digest):
            load_standalone_prompt_composition(artifact_base, run_id, digest=digest)  # typed
            terminal["state"] = MIGRATION_COMMITTED
        elif _is_legacy_omitted_thread_authority(current, run_id):
            if digest and _composition_is_published(artifact_base, run_id, digest):
                # F3: the publish landed, the rebind did not.  Roll the ORIGINAL prepared
                # attempt FORWARD -- re-bind the primary to its digest and commit under its
                # own identity -- rather than rolling it back and letting the anonymous
                # automatic path re-bind the already-published composition with actor="".
                upgraded = dict(current)
                upgraded["thread_id"] = DEFAULT_THREAD_ID
                upgraded["prompt_composition_digest"] = digest
                _durable_write(primary, json.dumps(upgraded, sort_keys=True, indent=2) + "\n")
                terminal["state"] = MIGRATION_COMMITTED
            else:
                terminal["state"] = MIGRATION_ROLLED_BACK
        else:
            raise LauncherError(
                f"{STANDALONE_MIGRATION_UNRECONCILABLE}: run {run_id!r} has an interrupted "
                f"prompt-composition upgrade {uid!r} attempt {attempt} binding digest "
                f"{digest!r}, but its primary authority is neither the legacy shape nor the "
                "upgraded identity bound to that digest; the attempt is left open and every "
                "authority read refuses rather than commit or roll back over unproven state")
        terminal["recorded_at"] = _authority_now()
        _durable_append(log, json.dumps(terminal, sort_keys=True) + "\n")


def _composition_is_published(artifact_base: Any, run_id: str, digest: str) -> bool:
    """Iteration-3 F3.  ``True`` when the content-addressed composition archive for
    ``digest`` exists AND validates through the production loader -- i.e. the two-phase
    upgrade writer's persist step (2) completed for this attempt before the crash.  A
    missing archive is ``False`` (crash before persist -> roll back); a present-but-corrupt
    archive propagates the typed :data:`STANDALONE_PROMPT_COMPOSITION_MISSING` refusal
    rather than being silently treated as unpublished."""
    if not digest:
        return False
    if not prompt_composition_archive_path(artifact_base, run_id, digest).exists():
        return False
    load_standalone_prompt_composition(artifact_base, run_id, digest=digest)   # typed refusal
    return True


def reconcile_authority_upgrades(artifact_base: Any, run_id: str) -> None:
    """Run :func:`_reconcile_authority_upgrades_locked` under the run's migration lock.
    Fast no-op when the run has no upgrade log at all."""
    if not _authority_upgrade_log_path(artifact_base, run_id).exists():
        return
    with _MigrationLock(artifact_base, run_id):
        _reconcile_authority_upgrades_locked(artifact_base, run_id)


def _legacy_authority_under_lock(artifact_base: Any, run_id: str
                                 ) -> tuple[dict[str, Any] | None, DurableThreadEvidence | None]:
    """The legacy omitted-thread authority record AND the durable evidence that proves it
    launched as ``launcher`` -- or ``(None, None)`` when the primary file is absent /
    unreadable / not the legacy shape / already upgraded, or when the evidence is absent
    / unreadable / another thread (each of those stays refused downstream by name; none
    is upgraded).  The CALLER holds the migration lock."""
    primary = standalone_authority_path(artifact_base, run_id, "")
    try:
        record = json.loads(primary.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not _is_legacy_omitted_thread_authority(record, run_id):
        return None, None
    evidence = durable_thread_evidence(artifact_base, run_id)
    if evidence.kind != THREAD_EVIDENCE_PRESENT or evidence.thread_id != DEFAULT_THREAD_ID:
        return None, None
    return record, evidence


def _write_upgraded_legacy_authority(artifact_base: Any, run_id: str, record: Mapping[str, Any],
                                     composition: Mapping[str, Any],
                                     evidence: DurableThreadEvidence, *,
                                     composition_source: str, actor: str = "",
                                     reason: str = "") -> dict[str, Any]:
    """Rewrite the legacy authority as the effective identity bound to ``composition``'s
    digest -- as a CRASH-CONSISTENT two-phase act (round-9 item 2), the same protocol
    :func:`migrate_standalone_profile` follows.  The CALLER holds the lock.

    1. ``prepared`` -- the durable intent, keyed by the operation's ``upgrade_id`` and
       this try's ``attempt``, appended and fsynced BEFORE anything changes;
    2. the composition is persisted content-addressed (an existing archive is validated,
       item 5);
    3. COMPARE-AND-SWAP: the primary authority is re-read and must still be the exact
       legacy shape this attempt was prepared against (under the lock it always is; the
       invariant is stated as code), then atomically rewritten;
    4. ``committed`` -- keyed by the same id / attempt.

    A crash at any boundary is reconciled on the next read
    (:func:`_reconcile_authority_upgrades_locked`): after the rebind -> rolled forward to
    ``committed``; before it -> ``rolled_back`` and the upgrade re-runs.  The single
    rewrite-then-append this replaced left the new composition bound with NO audit record
    after a crash between the two, and the replay was then refused because the authority
    was no longer the legacy shape.
    """
    primary = standalone_authority_path(artifact_base, run_id, "")
    digest = prompt_composition_digest(composition)
    uid = _upgrade_id(run_id, digest, composition_source, actor, reason)
    attempts = [a for (u, a) in _upgrade_attempt_states(artifact_base, run_id) if u == uid]
    attempt = (max(attempts) if attempts else 0) + 1
    base_record = {"schema": STANDALONE_AUTHORITY_UPGRADE_SCHEMA, "run_id": run_id,
                   "upgrade_id": uid, "attempt": attempt,
                   "from_thread_id": "", "to_thread_id": DEFAULT_THREAD_ID,
                   "bound_prompt_composition_digest": digest,
                   "bound_prompt_composer": str(composition.get("composer") or ""),
                   "composition_source": composition_source,
                   "actor": str(actor), "reason": str(reason),
                   "durable_evidence_source": evidence["source"]}
    log = _authority_upgrade_log_path(artifact_base, run_id)
    # (1) PREPARED intent, fsynced.
    _durable_append(log, json.dumps({**base_record, "state": MIGRATION_PREPARED,
                                     "prepared_at": _authority_now()},
                                    sort_keys=True) + "\n")
    # (2) the composition it binds, content-addressed (validated if already there).
    persist_standalone_prompt_composition(artifact_base, run_id, composition)
    # (3) CAS, then the atomic rewrite.
    try:
        current = json.loads(primary.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = None
    if not _is_legacy_omitted_thread_authority(current, run_id):
        _durable_append(log, json.dumps({**base_record, "state": MIGRATION_ROLLED_BACK,
                                         "recorded_at": _authority_now(),
                                         "cas_failed": True}, sort_keys=True) + "\n")
        raise LauncherError(
            f"{STANDALONE_MIGRATION_REFUSED}: run {run_id!r}'s primary authority moved "
            "before the legacy-authority upgrade could re-bind it; the attempt is rolled "
            "back and must be re-issued against the current authority")
    upgraded = dict(record)
    upgraded["thread_id"] = DEFAULT_THREAD_ID
    upgraded["prompt_composition_digest"] = digest
    _durable_write(primary, json.dumps(upgraded, sort_keys=True, indent=2) + "\n")
    # (4) COMMITTED, keyed by the same id / attempt.
    audit = {**base_record, "state": MIGRATION_COMMITTED, "recorded_at": _authority_now()}
    _durable_append(log, json.dumps(audit, sort_keys=True) + "\n")
    return audit


def _upgrade_legacy_authority_if_needed(artifact_base: Any, run_id: str,
                                        thread_id: str = "") -> None:
    """Recognise ONLY the exact legacy omitted-``thread_id`` authority whose durable
    evidence PROVES ``launcher`` and atomically upgrade it to the effective identity, under
    the SAME run-scoped inter-process authority lock migration uses.  B2.

    * Fast no-op unless the primary authority file exists and is the legacy shape (an
      unreadable file is left to the validated read, which refuses it by name).
    * Under the lock, re-read (another process may have upgraded already -> no-op).
    * The upgrade proceeds ONLY when :func:`durable_thread_evidence` is ``present`` and
      names :data:`DEFAULT_THREAD_ID`.  Absent / unreadable / any-other-thread leaves the
      record untouched (still refused downstream) -- no blanket acceptance of empty-thread
      authorities, which would reopen the foreign-authority bypass.
    * The now-required prompt-composition binding is recovered from the run's persisted
      composition.  **A legacy run that persisted NONE is REFUSED (round-8 item 4)**: the
      pre-fix launcher could compose the production prompt from an objective without
      persisting anything, so "no composition on disk" is AMBIGUOUS between "the launch
      delivered the canonical intent" and "the launch delivered the objective / role /
      output-contract prompt and never recorded it".  Inferring ``composer: none`` here
      silently downgraded every later dispatch of such a run to raw ``ActionIntent``
      JSON.  The upgrade now fails closed with the typed
      :data:`STANDALONE_PROMPT_COMPOSITION_MISSING` naming the remedy -- the explicit,
      audited :func:`migrate_standalone_prompt_composition` (CLI
      ``migrate-standalone-prompt-composition``), through which an operator SUPPLIES the
      composition (or declares ``composer: none`` deliberately, attributed and reasoned).
      A persisted composition that is present-but-unreadable propagates the same typed
      refusal.
    * The rewrite is atomic (``_durable_write``) and the act is journalled idempotently.
    """
    primary = standalone_authority_path(artifact_base, run_id, "")
    if not primary.exists():
        return
    try:
        record = json.loads(primary.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return                                           # the validated read refuses it
    if not _is_legacy_omitted_thread_authority(record, run_id):
        return
    with _MigrationLock(artifact_base, run_id):
        _reconcile_authority_upgrades_locked(artifact_base, run_id)
        record, evidence = _legacy_authority_under_lock(artifact_base, run_id)
        if record is None or evidence is None:
            return                                       # upgraded by a racer / not the case
        composition = load_standalone_prompt_composition(artifact_base, run_id)
        if composition is None:
            raise LauncherError(
                f"{STANDALONE_PROMPT_COMPOSITION_MISSING}: run {run_id!r} carries a legacy "
                "(pre-fix) launch authority and persisted NO prompt composition, so whether "
                "its launch delivered the production prompt (objective / role / output "
                "contract) or the canonical intent cannot be known from its durable state; "
                "the upgrade refuses rather than infer `composer: none` and dispatch raw "
                "ActionIntent JSON.  Supply the composition by the audited migration: "
                "`run_workflow.py migrate-standalone-prompt-composition --run-id "
                f"{run_id} --artifact-base ... --state <the launch state JSON> "
                "--objective <the launch objective> [--project-root ...] --actor-id ... "
                "--reason ...` (or `--composer-none` to declare, attributably, that the "
                "launch delivered the canonical intent), then recover it")
        _write_upgraded_legacy_authority(artifact_base, run_id, record, composition,
                                         evidence, composition_source="persisted")


def migrate_standalone_prompt_composition(artifact_base: Any, run_id: str, *,
                                          composition: Mapping[str, Any], actor: str,
                                          reason: str) -> dict[str, Any]:
    """The ONE sanctioned, audited way to SUPPLY the prompt composition of a legacy
    (pre-fix) run that persisted none -- round-8 item 4's remedy.

    Under the run's inter-process migration lock: the primary authority must be the
    exact legacy omitted-thread shape whose durable evidence proves ``launcher`` (the
    same gate the automatic upgrade applies; anything else is refused by name -- an
    already-bound run is never re-bound here, a foreign / unproven one never accepted).
    The supplied composition record is validated, persisted content-addressed, and the
    authority is atomically upgraded to the effective identity bound to its digest; the
    audit row names the actor, the reason and ``composition_source: audited_migration``.
    Replaying the identical migration (same digest, actor, reason) on the already-
    upgraded run returns the recorded audit row and writes nothing.
    """
    if not str(actor).strip() or not str(reason).strip():
        raise LauncherError(
            f"{STANDALONE_MIGRATION_REFUSED}: a prompt-composition migration requires a "
            "non-empty --actor-id and --reason; an unattributable binding is not an "
            "audited act")
    if (not isinstance(composition, Mapping)
            or composition.get("schema") != STANDALONE_PROMPT_COMPOSITION_SCHEMA
            or composition.get("composer") not in (PROMPT_COMPOSER_PRODUCTION,
                                                   PROMPT_COMPOSER_NONE)
            or (composition.get("composer") == PROMPT_COMPOSER_PRODUCTION
                and not str(composition.get("objective") or ""))):
        raise LauncherError(
            f"{STANDALONE_MIGRATION_REFUSED}: the supplied prompt composition is not a "
            "composition record (schema / composer / objective)")
    composition = dict(composition)
    digest = prompt_composition_digest(composition)
    with _MigrationLock(artifact_base, run_id):
        _reconcile_authority_upgrades_locked(artifact_base, run_id)   # item 2 (round 9)
        record, evidence = _legacy_authority_under_lock(artifact_base, run_id)
        if record is None or evidence is None:
            # Idempotent replay: the authority already binds THIS composition through a
            # recorded (COMMITTED) upgrade of this actor/reason.
            for audit in reversed(read_authority_upgrades(artifact_base, run_id)):
                if (audit.get("bound_prompt_composition_digest") == digest
                        and audit.get("composition_source") == "audited_migration"
                        and audit.get("actor") == str(actor)
                        and audit.get("reason") == str(reason)):
                    current = _load_authority_validated(artifact_base, run_id,
                                                        DEFAULT_THREAD_ID)
                    if (current or {}).get("prompt_composition_digest") == digest:
                        return dict(audit)
            raise LauncherError(
                f"{STANDALONE_MIGRATION_REFUSED}: run {run_id!r} records no legacy "
                "(pre-fix) launch authority lacking a prompt composition whose durable "
                "evidence proves the `launcher` thread; a run whose authority already "
                "binds a composition is never re-bound here, and one whose thread evidence "
                "is absent, unreadable or another thread's is not accepted")
        # Round-10 item 4.  The composition is NOT pre-published here.  The pre-fix order
        # persisted the composition BEFORE calling the two-phase writer (whose own protocol
        # writes `prepared` FIRST and persists the composition SECOND); a crash after this
        # outer persist but before the `prepared` record left the composition on disk with
        # no audited intent, so the AUTOMATIC upgrade (`_upgrade_legacy_authority_if_needed`)
        # would then find it and complete the upgrade as `composition_source=persisted,
        # actor=""`, silently dropping the requested actor/reason and making this migration's
        # replay refuse.  The two-phase `_write_upgraded_legacy_authority` is now the ONLY
        # publication path: `prepared -> persist/rebind -> committed`, with actor/reason
        # preserved across every crash cut and on replay.
        return _write_upgraded_legacy_authority(
            artifact_base, run_id, record, composition, evidence,
            composition_source="audited_migration", actor=actor, reason=reason)


def check_standalone_authority(artifact_base: Any, run_id: str, *, runtime_state_path: Any,
                               thread_id: str = "",
                               approval_authority: str = "none",
                               profile_digest: str = "",
                               prompt_composition_digest: str = "") -> dict[str, Any] | None:
    """READ-ONLY exact-match check: the recorded binding for this run/thread, or ``None``
    when none is recorded; a recorded binding that DIFFERS raises
    ``STANDALONE_AUTHORITY_CONFLICT``.  Writes nothing.  CORRECTION 2 split this out of
    :func:`persist_standalone_authority` so composition can refuse a conflicting relaunch
    before any claim while the CREATE is deferred until after the claim succeeds.  This is
    a WRITE-side check -- the launcher inspecting its own relaunch -- so it resolves the
    path with ``for_write=True`` (B1): a second thread's check reads its own per-thread
    file rather than the primary."""
    target = standalone_authority_path(artifact_base, run_id, thread_id, for_write=True)
    if not target.exists():
        return None
    wanted = _authority_record(run_id, runtime_state_path=runtime_state_path,
                               thread_id=thread_id, approval_authority=approval_authority,
                               profile_digest=profile_digest,
                               prompt_composition_digest=prompt_composition_digest)
    try:
        current = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LauncherError(
            f"{STANDALONE_ADAPTER_REQUIRES_LEDGER}: the persisted runtime-state "
            f"authority at {target} is unreadable ({exc})") from exc
    except ValueError as exc:
        raise LauncherError(
            f"{STANDALONE_AUTHORITY_CONFLICT}: the persisted runtime-state authority "
            f"at {target} is not readable JSON ({exc}); it is not overwritten") from exc
    if current == wanted:
        return current
    differing = sorted(key for key in set(wanted) | set(current or {})
                       if (current or {}).get(key) != wanted.get(key))
    raise LauncherError(
        f"{STANDALONE_AUTHORITY_CONFLICT}: run {run_id!r} (thread {thread_id!r}) "
        f"already records a different launch binding at {target} "
        f"(differs in {', '.join(differing)}); the recorded binding is kept and a "
        "migration must be an explicit act on that record")


def publish_standalone_launch_bindings(artifact_base: Any, run_id: str, *,
                                       profile_spec: Mapping[str, Any],
                                       runtime_state_path: Any, thread_id: str = "",
                                       approval_authority: str = "none",
                                       prompt_composition: Mapping[str, Any] | None = None
                                       ) -> None:
    """The ONE write of a standalone run's launch bindings: the profile, the PROMPT
    COMPOSITION (round-7 blocker 2), then the create-once / exact-match authority record
    that BINDS both digests.  CORRECTION 2: called by `execute_state` only after the
    run-scoped execution authority has been claimed SUCCESSFULLY -- so an invocation
    refused `EXECUTION_AUTHORITY_HELD` can never create or change the binding a Watchdog
    will recover from -- and before any spawn, so a recovery of anything this launch does
    can rebuild its runtime AND its production prompt.  An in-memory ledger names no path
    and records no binding (the profile and the composition are still kept).

    ``prompt_composition`` is the composition RECORD (:func:`prompt_composition_record`);
    ``None`` means the launch supplied none, which is persisted as the launch's OWN
    ``composer: none`` declaration -- distinct, by record, from a composition that was
    lost.
    """
    persist_standalone_profile(artifact_base, run_id, profile_spec)
    composition = (dict(prompt_composition) if prompt_composition is not None
                   else prompt_composition_record(None))
    persist_standalone_prompt_composition(artifact_base, run_id, composition)
    if runtime_state_path is not None:
        persist_standalone_authority(artifact_base, run_id,
                                     runtime_state_path=runtime_state_path,
                                     thread_id=thread_id,
                                     approval_authority=approval_authority,
                                     profile_digest=profile_digest(profile_spec),
                                     prompt_composition_digest=prompt_composition_digest(
                                         composition))


def _read_profile_file(target: Path) -> dict[str, Any] | None:
    if not target.exists():
        return None
    try:
        spec = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LauncherError(
            f"{STANDALONE_ADAPTER_REQUIRES_PROFILE}: the persisted profile at {target} is "
            f"unreadable ({exc})") from exc
    if not isinstance(spec, dict):
        raise LauncherError(
            f"{STANDALONE_ADAPTER_REQUIRES_PROFILE}: the persisted profile at {target} is "
            "not a JSON object")
    return spec


def load_standalone_profile(artifact_base: Any, run_id: str,
                            *, digest: str = "") -> dict[str, Any] | None:
    """The persisted profile spec, or ``None`` when the run never persisted one.

    ``digest`` (finding 2) reads the CONTENT-ADDRESSED archive a run/thread authority
    bound -- `profiles/<digest>.json` -- so a recovery rebuilds the runtime from the
    profile THAT thread launched, not from the run-global `profile.json` a later thread of
    the same run overwrote.  The archive's own content is verified against the digest it is
    named by, so a corrupted archive is refused rather than silently rebuilt.  Without a
    digest it reads `profile.json`, which is the in-memory-ledger / single-composition
    case that records no authority binding at all."""
    if digest:
        archive = profile_archive_path(artifact_base, run_id, digest)
        spec = _read_profile_file(archive)
        if spec is None:
            raise LauncherError(
                f"{STANDALONE_ADAPTER_REQUIRES_PROFILE}: run {run_id!r} bound profile "
                f"digest {digest!r} but its archive {archive} is missing; the launch "
                "profile cannot be rebuilt")
        if profile_digest(spec) != digest:
            raise LauncherError(
                f"{STANDALONE_ADAPTER_REQUIRES_PROFILE}: the profile archive {archive} "
                f"does not hash to the digest {digest!r} the launch bound; it is refused "
                "rather than rebuilt from")
        _refuse_legacy_unfrozen_archive(spec, run_id, digest, archive)
        return spec
    spec = _read_profile_file(standalone_profile_path(artifact_base, run_id))
    if spec is not None:
        _refuse_legacy_unfrozen_archive(spec, run_id, "",
                                        standalone_profile_path(artifact_base, run_id))
    return spec


def _refuse_legacy_unfrozen_archive(spec: Mapping[str, Any], run_id: str, digest: str,
                                    archive: Path) -> None:
    """Iteration 5, B1 / round-8 item 3: the READ door.  An archive holding a relative
    OR OMITTED ``worktree`` (or a relative ``add_dirs`` entry) was written by the PRE-fix
    model (the write door refuses one now).  Its bytes meant "the launch cwd" and no
    durable record of that cwd exists for such a run, so NOTHING here may guess: not this
    process's cwd (the reviewer's ``<tmp>/recovery/wt`` probe, and the omitted-worktree
    run recovered from B) and not any other directory.  The run is recovered only
    through the explicit, audited profile migration, which binds the launch-time absolute
    worktree the operator names and leaves the audit trail of who re-bound what and why.
    The refusal is the same on resume, recover, cancel, abandon and watchdog, because all
    of them rebuild through this one read."""
    _refuse_unfrozen_profile(
        spec, where=(f"the persisted profile archive {archive} bound by run {run_id!r}"
                     + (f" (digest {digest!r})" if digest else "")),
        remedy="it was archived by the pre-fix model; re-bind the run with `run_workflow.py "
               "migrate-standalone-profile --run-id ... --standalone-profile <the same "
               "profile with the launch-time ABSOLUTE worktree> --actor-id ... --reason "
               "...` (audited) and then recover it")


# ---- round-7 blocker 2: the PROMPT COMPOSITION a launch persists for its recoveries ----
def prompt_composition_record(objective: str | None, *,
                              requested_phases: tuple[str, ...] = (),
                              risk: str = "high",
                              project_root: Any = None,
                              role_instructions: Mapping[str, Any] | None = None
                              ) -> dict[str, Any]:
    """The NON-SECRET inputs :func:`build_standalone_prompt_composer` needs, as ONE durable
    record.  ``objective=None`` (or empty) declares ``composer: none`` -- the launch named
    no task contract and delivers the canonical intent payload -- which is the launch's
    own declaration and is recorded as such.  Everything else is exactly what the launch
    handed the composer: the task contract, the requested phases, the risk, the ABSOLUTE
    project root the quality profile is read under (blocker 7) and the role instructions
    the launch supplied as data.  No secret can appear here: none of these inputs is one.
    """
    if not objective:
        return {"schema": STANDALONE_PROMPT_COMPOSITION_SCHEMA,
                "composer": PROMPT_COMPOSER_NONE}
    return {"schema": STANDALONE_PROMPT_COMPOSITION_SCHEMA,
            "composer": PROMPT_COMPOSER_PRODUCTION,
            "objective": str(objective),
            "requested_phases": [str(phase) for phase in requested_phases],
            "risk": str(risk),
            "project_root": (str(Path(project_root).resolve())
                             if project_root is not None else None),
            "role_instructions": {str(key): str(value)
                                  for key, value in dict(role_instructions or {}).items()}}


def prompt_composition_payload(composition: Mapping[str, Any]) -> str:
    """The canonical serialisation of a composition record -- the bytes its digest names."""
    return json.dumps(dict(composition), sort_keys=True, indent=2, ensure_ascii=False)


def prompt_composition_digest(composition: Mapping[str, Any]) -> str:
    """The content address the run/thread authority binds (blocker 2), computed over
    :func:`prompt_composition_payload` exactly as :func:`profile_digest` is over the
    profile, so a recovery can VERIFY the archive it rebuilds from."""
    import hashlib
    return hashlib.sha256(prompt_composition_payload(composition).encode("utf-8")
                          ).hexdigest()[:16]


def standalone_prompt_composition_path(artifact_base: Any, run_id: str) -> Path:
    """``standalone/prompt_composition.json`` -- the CURRENT composition, beside the
    profile, for a run that recorded no authority binding (in-memory ledger)."""
    return standalone_profile_path(artifact_base, run_id).with_name("prompt_composition.json")


def prompt_composition_archive_path(artifact_base: Any, run_id: str, digest: str) -> Path:
    """``standalone/prompt_compositions/<digest>.json`` -- write-once, content-addressed."""
    return (standalone_profile_path(artifact_base, run_id).parent / "prompt_compositions"
            / f"{digest}.json")


def persist_standalone_prompt_composition(artifact_base: Any, run_id: str,
                                          composition: Mapping[str, Any]) -> Path:
    """Write the composition durably: the content-addressed archive (write-once) and the
    run's current composition (rewritten only when it changes) -- the same two-file
    discipline as :func:`persist_standalone_profile`, for the same reason."""
    target = standalone_prompt_composition_path(artifact_base, run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = prompt_composition_payload(composition) + "\n"
    digest = prompt_composition_digest(composition)
    archive = prompt_composition_archive_path(artifact_base, run_id, digest)
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        _durable_write(archive, payload)
    else:
        # Item 5 (round 9), the same door for the composition: an existing archive is
        # validated through the production loader (digest + record shape) before a
        # launch or a migration binds authority to its digest.
        load_standalone_prompt_composition(artifact_base, run_id, digest=digest)
    current = None
    if target.exists():
        try:
            current = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise LauncherError(
                f"{STANDALONE_PROMPT_COMPOSITION_MISSING}: the persisted prompt composition "
                f"at {target} is unreadable ({exc})") from exc
    if current != payload:
        _durable_write(target, payload)
    return target


def _read_composition_file(target: Path, run_id: str) -> dict[str, Any] | None:
    if not target.exists():
        return None
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LauncherError(
            f"{STANDALONE_PROMPT_COMPOSITION_MISSING}: run {run_id!r}'s persisted prompt "
            f"composition at {target} is unreadable ({exc}); the production prompt cannot "
            "be rebuilt and recovery is refused rather than dispatching raw intent JSON"
        ) from exc
    if (not isinstance(record, dict)
            or record.get("schema") != STANDALONE_PROMPT_COMPOSITION_SCHEMA
            or record.get("composer") not in (PROMPT_COMPOSER_PRODUCTION,
                                              PROMPT_COMPOSER_NONE)):
        raise LauncherError(
            f"{STANDALONE_PROMPT_COMPOSITION_MISSING}: run {run_id!r}'s persisted prompt "
            f"composition at {target} is not a composition record; the production prompt "
            "cannot be rebuilt and recovery is refused rather than dispatching raw intent "
            "JSON")
    return record


def load_standalone_prompt_composition(artifact_base: Any, run_id: str, *,
                                       digest: str = "") -> dict[str, Any] | None:
    """The persisted composition record.  With a ``digest`` (the one the run/thread
    authority bound) the content-addressed archive is read and VERIFIED against it, and a
    missing / unreadable / mismatching archive is the typed refusal
    :data:`STANDALONE_PROMPT_COMPOSITION_MISSING`.  Without one -- a run that recorded no
    authority binding -- the current ``prompt_composition.json`` is read, and ``None`` is
    returned only when that file does not exist (a run that was composed but never
    launched persisted nothing at all)."""
    if digest:
        archive = prompt_composition_archive_path(artifact_base, run_id, digest)
        record = _read_composition_file(archive, run_id)
        if record is None:
            raise LauncherError(
                f"{STANDALONE_PROMPT_COMPOSITION_MISSING}: run {run_id!r} bound prompt "
                f"composition digest {digest!r} but its archive {archive} is missing; the "
                "production prompt cannot be rebuilt and recovery is refused rather than "
                "dispatching raw intent JSON")
        if prompt_composition_digest(record) != digest:
            raise LauncherError(
                f"{STANDALONE_PROMPT_COMPOSITION_MISSING}: the prompt composition archive "
                f"{archive} does not hash to the digest {digest!r} the launch bound; it is "
                "refused rather than rebuilt from")
        return record
    return _read_composition_file(standalone_prompt_composition_path(artifact_base, run_id),
                                  run_id)


def composer_from_composition(composition: Mapping[str, Any] | None) -> Any:
    """The prompt composer a persisted composition record describes: ``None`` for a
    ``composer: none`` declaration (or no record at all), else the production renderer
    rebuilt through :func:`build_standalone_prompt_composer` from the SAME inputs the
    launch used -- objective, phases, risk, project root and role instructions."""
    if composition is None or composition.get("composer") != PROMPT_COMPOSER_PRODUCTION:
        return None
    root = composition.get("project_root")
    return build_standalone_prompt_composer(
        objective=str(composition.get("objective") or ""),
        requested_phases=tuple(composition.get("requested_phases") or ()),
        risk=str(composition.get("risk") or "high"),
        project_root=Path(root) if root else None,
        role_instructions=dict(composition.get("role_instructions") or {}))


def standalone_migration_log_path(artifact_base: Any, run_id: str) -> Path:
    """The durable, append-only audit log of profile migrations for a run.

    Under the run's own artifact root beside the profile and authority, so a stranger
    process (an auditor, a later recovery) can read the full history of who re-bound the
    profile, from which digest to which, and why."""
    return standalone_profile_path(artifact_base, run_id).with_name("profile_migrations.ndjson")


def read_standalone_migrations(artifact_base: Any, run_id: str,
                               thread_id: str = "") -> tuple[dict[str, Any], ...]:
    """Every recorded migration record for this run (optionally filtered to one thread),
    oldest first -- ``prepared``, ``committed`` and ``rolled_back`` alike.  RAISES on an
    unreadable / unparsable log rather than pretending there were none."""
    path = standalone_migration_log_path(artifact_base, run_id)
    if not path.exists():
        return ()
    from .standalone_capture import protocol_lines
    try:
        lines = protocol_lines(path.read_text(encoding="utf-8"))   # round-8 item 5
    except OSError as exc:
        raise LauncherError(
            f"{STANDALONE_MIGRATION_REFUSED}: the migration audit log at {path} is "
            f"unreadable ({exc})") from exc
    out: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise LauncherError(
                f"{STANDALONE_MIGRATION_REFUSED}: the migration audit log at {path} has an "
                f"unparsable entry ({exc})") from exc
        if not thread_id or record.get("thread_id") == thread_id:
            out.append(record)
    return tuple(out)


def standalone_committed_migrations(artifact_base: Any, run_id: str,
                                    thread_id: str = "") -> tuple[dict[str, Any], ...]:
    """The COMMITTED migration records -- the truthful history of profile re-binds.  A
    ``prepared`` with no ``committed`` is an interrupted attempt and is NOT a migration."""
    return tuple(r for r in read_standalone_migrations(artifact_base, run_id, thread_id)
                 if r.get("state") == MIGRATION_COMMITTED)


def _migration_id(run_id: str, thread_id: str, old_digest: str, new_digest: str,
                  actor: str, reason: str, epoch: int) -> str:
    """The attributable operation identity / replay key (F-001; hardened for iteration-5 B1).

    The SAME operation always hashes to the same id, so a crash retry is recognised and
    never appends a second committed record.  What makes two migrations "the same operation"
    now includes the AUTHORITY EPOCH it applies to -- both the validated source digest
    (``old_digest``) and a monotonic ``epoch`` ordinal (the count of committed migrations
    before it).  Without the epoch, a legitimate A->B, B->A, A->B history would give the two
    A->B operations one id and the second could be mistaken for a replay of the first (the
    iteration-5 blocking finding B1).  A different actor, reason, source, target OR epoch is
    a different operation."""
    import hashlib
    payload = json.dumps({"run_id": run_id, "thread_id": thread_id,
                          "old_profile_digest": str(old_digest),
                          "new_profile_digest": str(new_digest), "actor": str(actor),
                          "reason": str(reason), "epoch": int(epoch)}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _migration_attempt_states(artifact_base: Any, run_id: str, thread_id: str
                              ) -> dict[tuple[str, int], dict[str, dict[str, Any]]]:
    """``(migration_id, attempt) -> {state -> record}`` for this run/thread, in log order.

    Round-8 item 7.  The migration id names the OPERATION (source epoch, target, actor,
    reason); the ``attempt`` ordinal names one TRY of it.  A rolled-back attempt and the
    retry that follows it share the id and must not share a terminal state: grouping by id
    alone let a historical ``rolled_back`` make the retry's ``prepared`` look terminal, so
    a crash after the retry's re-bind left the new authority installed with no committed
    record, forever.  Every record this tree writes carries ``attempt``; a record written
    before the field existed is assigned positionally -- each attempt-less ``prepared``
    opens the next attempt of its id and an attempt-less terminal closes the latest -- so
    a legacy log reconciles by the same per-attempt rule."""
    grouped: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    latest: dict[str, int] = {}
    for rec in read_standalone_migrations(artifact_base, run_id, thread_id):
        mid, state = rec.get("migration_id"), rec.get("state")
        if not mid or not state:
            continue
        mid = str(mid)
        attempt = rec.get("attempt")
        if isinstance(attempt, int) and attempt >= 1:
            latest[mid] = max(latest.get(mid, 0), attempt)
        elif state == MIGRATION_PREPARED:
            attempt = latest.get(mid, 0) + 1
            latest[mid] = attempt
        else:
            attempt = latest.get(mid, 1)
            latest[mid] = attempt
        grouped.setdefault((mid, int(attempt)), {})[str(state)] = rec
    return grouped


def _next_migration_attempt(artifact_base: Any, run_id: str, thread_id: str,
                            migration_id: str) -> int:
    """The attempt ordinal a new try of ``migration_id`` records: one past the latest
    attempt the log holds for it (1 for a first try).  The caller holds the lock."""
    attempts = [attempt for (mid, attempt) in
                _migration_attempt_states(artifact_base, run_id, thread_id) if mid == migration_id]
    return (max(attempts) if attempts else 0) + 1


def _validated_profile_archive(artifact_base: Any, run_id: str, digest: str) -> dict[str, Any]:
    """The content-addressed profile archive ``digest`` names, loaded through the
    PRODUCTION loader (`load_standalone_profile`: present, hashes to its digest, frozen)
    AND validated as a profile (`profile_from_mapping`: the schema every launch and
    recovery applies).  Raises the loader's / schema's typed refusal; never ``None``."""
    from .standalone_profile import profile_from_mapping
    spec = load_standalone_profile(artifact_base, run_id, digest=digest)
    if spec is None:                                     # pragma: no cover - loader raises
        raise LauncherError(
            f"{STANDALONE_ADAPTER_REQUIRES_PROFILE}: run {run_id!r} has no profile archive "
            f"for digest {digest!r}")
    try:
        profile_from_mapping(spec)
    except Exception as exc:  # noqa: BLE001 - a malformed archive is refused by name
        raise LauncherError(
            f"{STANDALONE_ADAPTER_REQUIRES_PROFILE}: the profile archive for digest "
            f"{digest!r} of run {run_id!r} is not a valid profile ({exc})") from exc
    return spec


def _append_migration_record(artifact_base: Any, run_id: str, record: dict[str, Any]) -> None:
    log = standalone_migration_log_path(artifact_base, run_id)
    log.parent.mkdir(parents=True, exist_ok=True)
    _durable_append(log, json.dumps(record, sort_keys=True) + "\n")


def standalone_migration_lock_path(artifact_base: Any, run_id: str) -> Path:
    """The run-scoped INTER-PROCESS lock file every migration and reconciliation of this
    run's profile authority takes (round-7 blocker 5).  Beside the log it serialises."""
    return standalone_profile_path(artifact_base, run_id).with_name("profile_migrations.lock")


class _MigrationLock:
    """A real ``fcntl.flock`` on the run's lock file, held for the WHOLE of a migration
    or a reconciliation (round-7 consolidated review, blocker 5).

    Two interleavings were possible without it.  A recovery READ between the migrator's
    authority re-bind and its committed append reconciled the operation first -- rolling
    it forward -- and the migrator then appended the same terminal record, leaving
    ``prepared -> committed -> committed``.  And two concurrent migrators read the same
    old digest and epoch, both re-bound, and both committed a non-linear history.  The
    lock is exclusive and process-scoped (a second open file description of the same
    file blocks, so two threads of one process serialise as well), released on every
    exit including a crash, and taken by BOTH the writer and the reader side so neither
    can observe the other mid-operation.  Not re-entrant: :func:`migrate_standalone_profile`
    reconciles through the ``_locked`` variant while it holds the lock.
    """

    def __init__(self, artifact_base: Any, run_id: str) -> None:
        self.path = standalone_migration_lock_path(artifact_base, run_id)
        self._fd = -1

    def __enter__(self) -> "_MigrationLock":
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        except BaseException:
            os.close(self._fd)
            self._fd = -1
            raise
        return self

    def __exit__(self, *_exc: Any) -> None:
        import fcntl
        if self._fd >= 0:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = -1


def reconcile_standalone_migrations(artifact_base: Any, run_id: str,
                                    thread_id: str = "") -> None:
    """Deterministically finish or roll back every interrupted migration (F-001).

    A ``prepared`` record with neither ``committed`` nor ``rolled_back`` is an attempt that
    crashed between durable-write boundaries.  Its terminal state is decided ONLY by the
    authority's current digest -- the single source of truth:

    * authority already names the prepared ``new_profile_digest`` AND that profile's
      content-addressed archive exists  -> the re-bind is durably done, so append the
      ``committed`` record (ROLL FORWARD);
    * otherwise (authority still names the old digest, or the archive is missing)  -> the
      re-bind never durably happened, so append a ``rolled_back`` record and leave the
      authority on its old digest (ROLL BACK).

    Append-only and idempotent: a migration already terminal is skipped, so a second
    reconciliation (or a reconciliation racing a replay) adds nothing.  Fast no-op when the
    run has no migration log at all -- which is every run that never migrated.

    Round-7 blocker 5: the reconciliation runs UNDER the run's inter-process migration
    lock, so it can never observe a live migrator between its re-bind and its committed
    append (and finish the operation out from under it); an interrupted attempt it does
    observe is one whose migrator is gone."""
    if not standalone_migration_log_path(artifact_base, run_id).exists():
        return
    with _MigrationLock(artifact_base, run_id):
        _reconcile_standalone_migrations_locked(artifact_base, run_id, thread_id)


def _reconcile_standalone_migrations_locked(artifact_base: Any, run_id: str,
                                            thread_id: str = "") -> None:
    """The body of :func:`reconcile_standalone_migrations`; the caller HOLDS the lock.

    Per ATTEMPT (round-8 item 7), and decided only from VALID durable state (item 8):

    * the authority is read through the validated loader -- an unreadable, malformed or
      wrong-thread record is its own typed refusal and PROPAGATES (it used to collapse to
      ``None`` and roll the attempt back with no proof of anything);
    * authority == the attempt's target digest -> the target archive is loaded through
      the PRODUCTION loader and validated as a profile; only then ``committed``.  A
      missing / corrupt / non-profile archive is a typed refusal and the attempt stays
      open (it used to commit because the file merely existed, after which every profile
      load of the run refused);
    * authority == the attempt's SOURCE digest -> POSITIVE proof the re-bind never landed
      -> ``rolled_back``;
    * anything else (a third digest, no authority record) is
      :data:`STANDALONE_MIGRATION_UNRECONCILABLE`.
    """
    if not standalone_migration_log_path(artifact_base, run_id).exists():
        return
    for (mid, attempt), by_state in _migration_attempt_states(artifact_base, run_id,
                                                              thread_id).items():
        if MIGRATION_PREPARED not in by_state:
            continue
        if MIGRATION_COMMITTED in by_state or MIGRATION_ROLLED_BACK in by_state:
            continue                                     # this attempt is terminal
        prepared = by_state[MIGRATION_PREPARED]
        thread = str(prepared.get("thread_id") or "")
        old_digest = str(prepared.get("old_profile_digest") or "")
        new_digest = str(prepared.get("new_profile_digest") or "")
        current = _load_authority_validated(artifact_base, run_id, thread)  # typed refusal
        current_digest = str((current or {}).get("profile_digest") or "")
        terminal = dict(prepared)
        terminal["attempt"] = attempt
        terminal["reconciled"] = True
        if current is not None and current_digest and current_digest == new_digest:
            _validated_profile_archive(artifact_base, run_id, new_digest)   # typed refusal
            terminal["state"] = MIGRATION_COMMITTED       # roll forward, over VALID state
        elif current is not None and current_digest and current_digest == old_digest:
            terminal["state"] = MIGRATION_ROLLED_BACK     # roll back, with positive proof
        else:
            raise LauncherError(
                f"{STANDALONE_MIGRATION_UNRECONCILABLE}: run {run_id!r} (thread "
                f"{thread!r}) has an interrupted profile migration {mid!r} attempt "
                f"{attempt} from digest {old_digest!r} to {new_digest!r}, but its authority "
                + ("records no profile binding" if current is None
                   else f"names digest {current_digest!r}, which is neither")
                + "; the attempt is left open and recovery is refused rather than "
                "committed or rolled back over unproven state")
        terminal["recorded_at"] = _authority_now()
        _append_migration_record(artifact_base, run_id, terminal)


def migrate_standalone_profile(artifact_base: Any, run_id: str, *, thread_id: str = "",
                               new_profile_spec: Mapping[str, Any], actor: str,
                               reason: str) -> dict[str, Any]:
    """The ONE sanctioned, audited, CRASH-CONSISTENT, REPLAY-SAFE way to re-bind a
    run/thread's profile.  B4 + Final Adversarial Review F-001.

    The immutable launch authority is create-once and exact-digest by default (F2/F9): a
    ``--standalone-profile`` that does not restate the bound digest is refused.  A
    legitimate change is a DELIBERATE, ATTRIBUTABLE, TWO-PHASE act keyed by a stable
    migration id (:func:`_migration_id`):

    1. reconcile any interrupted prior migration (roll forward / back), then
    2. if the authority ALREADY names the requested target and a committed operation of this
       shape (same target, actor, reason) produced it -> IDEMPOTENT: return it, append
       nothing.  This gate is authority-consistency, not id-presence (iteration-5 B1): a
       distinct later migration at a different epoch is never mistaken for an old replay;
    3. append a ``prepared`` record (the intent, keyed by the epoch-bound id) and fsync it;
    4. archive the new profile content-addressed (write-once);
    5. atomically re-bind the authority record (tmp + rename + dir fsync) to the new digest;
    6. append the ``committed`` record keyed by the same id.

    A crash at ANY boundary is reconciled deterministically from the authority digest on
    the next read (:func:`reconcile_standalone_migrations` / :func:`load_standalone_authority`):
    a ``prepared`` whose re-bind landed becomes ``committed``, one whose re-bind did not
    becomes ``rolled_back`` -- so there is exactly ONE ``committed`` record per operation and
    no ghost completion.  A missing actor/reason, or a no-op to the bound digest, is refused.
    """
    if not str(actor).strip() or not str(reason).strip():
        raise LauncherError(
            f"{STANDALONE_MIGRATION_REFUSED}: a profile migration requires a non-empty "
            "--actor-id and --reason; an unattributable re-bind is not an audited act")
    # Iteration 5, B1: the migration's NEW profile is frozen at its own door, against the
    # migrating process's cwd -- the operator's explicit act names the directory as the
    # operator means it -- so the archive it writes is absolute like every other and a
    # legacy relative archive is re-bound to the launch-time absolute worktree the operator
    # names, never to a re-interpretation of the old bytes.
    new_profile_spec = freeze_profile_worktree(new_profile_spec)
    # Round-7 blocker 5: the WHOLE operation -- reconcile, read, prepare, archive, CAS
    # re-bind, commit -- runs under the run's inter-process lock, so no reader reconciles
    # it half-way and no second migrator interleaves with it.
    with _MigrationLock(artifact_base, run_id):
        return _migrate_standalone_profile_locked(
            artifact_base, run_id, thread_id=thread_id, new_profile_spec=new_profile_spec,
            actor=actor, reason=reason)


def _migrate_standalone_profile_locked(artifact_base: Any, run_id: str, *, thread_id: str,
                                       new_profile_spec: Mapping[str, Any], actor: str,
                                       reason: str) -> dict[str, Any]:
    """The body of :func:`migrate_standalone_profile`; the caller HOLDS the lock."""
    _reconcile_standalone_migrations_locked(artifact_base, run_id, thread_id)
    record = _load_authority_validated(artifact_base, run_id, thread_id)
    if record is None:
        raise LauncherError(
            f"{STANDALONE_MIGRATION_REFUSED}: run {run_id!r} (thread {thread_id!r}) records "
            "no standalone launch binding to migrate")
    # Blocker 1: the operation is attributed to the thread the authority ACTUALLY binds
    # (the effective identity), never to an empty caller-supplied one.
    thread_id = str(record["thread_id"])
    new_digest = profile_digest(new_profile_spec)
    old_digest = str(record["profile_digest"])
    committed_records = standalone_committed_migrations(artifact_base, run_id, thread_id)
    # ---- Idempotent replay, GATED ON AUTHORITY-CONSISTENCY (iteration-5 B1) --------------
    # A committed operation is a replay of THIS request ONLY when the current authority
    # already names the target that operation produced (authority == its new digest).  So a
    # legitimate A->B, B->A, A->B history is safe: the third A->B reads authority == A, which
    # is not the target B, so it is NOT mistaken for a replay of the first A->B -- it is a
    # fresh migration and the authority actually moves.  The previous key returned any
    # historical committed record with a matching id before checking the authority, leaving
    # the authority on the wrong profile.
    if old_digest == new_digest:
        for committed in reversed(committed_records):
            if (str(committed.get("new_profile_digest") or "") == new_digest
                    and str(committed.get("actor") or "") == str(actor)
                    and str(committed.get("reason") or "") == str(reason)):
                return committed                         # authority already at this op's target
        raise LauncherError(
            f"{STANDALONE_MIGRATION_REFUSED}: the new profile hashes to the digest already "
            f"bound ({old_digest!r}); there is nothing to migrate")
    # Refuse a malformed profile BEFORE any durable write (not at the next recovery).
    from .standalone_profile import profile_from_mapping
    try:
        profile_from_mapping(new_profile_spec)
    except Exception as exc:  # noqa: BLE001 - a malformed migration profile refuses now
        raise LauncherError(
            f"{STANDALONE_MIGRATION_REFUSED}: the new profile is invalid ({exc})") from exc
    # The operation identity binds the validated source epoch (old_digest) AND a monotonic
    # ordinal (the count of committed migrations so far), so two same-shape migrations at
    # DIFFERENT epochs never alias -- and a crash retry of THIS one, before it commits, reads
    # the same committed count and so recomputes the same id (a rolled-back attempt added no
    # committed record; a rolled-forward one is caught by the replay gate above).
    epoch = len(committed_records)
    mid = _migration_id(run_id, thread_id, old_digest, new_digest, actor, reason, epoch)
    # Round-8 item 7: this TRY of the operation gets its own attempt ordinal, so a retry
    # after a rolled-back attempt is reconciled on its own state, never on the history
    # of the attempt it replaces.
    attempt = _next_migration_attempt(artifact_base, run_id, thread_id, mid)
    base_record = {"schema": STANDALONE_MIGRATION_SCHEMA, "migration_id": mid,
                   "attempt": attempt,
                   "run_id": run_id, "thread_id": thread_id, "operation_epoch": epoch,
                   "old_profile_digest": old_digest, "new_profile_digest": new_digest,
                   "actor": str(actor), "reason": str(reason)}
    # (3) PREPARED intent, fsynced -- the durable record that a re-bind was ABOUT to happen.
    _append_migration_record(artifact_base, run_id,
                             {**base_record, "state": MIGRATION_PREPARED,
                              "prepared_at": _authority_now()})
    # (4) archive the new profile (write-once, content-addressed).
    persist_standalone_profile(artifact_base, run_id, new_profile_spec)
    # (5) COMPARE-AND-SWAP, then atomically re-bind the authority to the new digest.
    # Blocker 5: the authority and the committed epoch are RE-READ under the lock right
    # before the re-bind and must still be the ones this operation was prepared against;
    # any movement means another writer got in and this attempt is refused (its prepared
    # record is rolled back by name) rather than re-binding over a digest it never
    # validated.  Under the lock this cannot fire; it is the invariant stated as code.
    current = _load_authority_validated(artifact_base, run_id, thread_id)
    current_digest = str((current or {}).get("profile_digest") or "")
    current_epoch = len(standalone_committed_migrations(artifact_base, run_id, thread_id))
    if current_digest != old_digest or current_epoch != epoch:
        _append_migration_record(artifact_base, run_id,
                                 {**base_record, "state": MIGRATION_ROLLED_BACK,
                                  "recorded_at": _authority_now(),
                                  "cas_failed": {"expected_digest": old_digest,
                                                 "observed_digest": current_digest,
                                                 "expected_epoch": epoch,
                                                 "observed_epoch": current_epoch}})
        raise LauncherError(
            f"{STANDALONE_MIGRATION_REFUSED}: run {run_id!r} (thread {thread_id!r}) "
            f"authority moved from digest {old_digest!r} / epoch {epoch} to "
            f"{current_digest!r} / epoch {current_epoch} before the re-bind; the attempt "
            "is rolled back and must be re-issued against the current authority")
    migrated = dict(record)
    migrated["profile_digest"] = new_digest
    _durable_write(standalone_authority_path(artifact_base, run_id, thread_id, for_write=True),
                   json.dumps(migrated, sort_keys=True, indent=2) + "\n")
    # (6) COMMITTED record keyed by the same id -- the migration is now durable and truthful.
    committed = {**base_record, "state": MIGRATION_COMMITTED,
                 "migrated_at": _authority_now()}
    _append_migration_record(artifact_base, run_id, committed)
    return committed


def build_standalone_runtime(artifact_base: Any, run_id: str, *, runtime_state: Any,
                             profile_spec: Mapping[str, Any], journal: Any) -> Any:
    """The ONE place a `StandaloneRuntime` is composed from a profile spec.

    Shared by the launcher (a fresh run) and the Watchdog (a recovery of a stalled run),
    so the runtime a recovery re-enters with is built by the same code that built the
    original -- the same profile validation, the same declared worktree, the same journal.
    """
    from .standalone_profile import profile_from_mapping
    from .standalone_runtime import StandaloneRuntime
    try:
        profile = profile_from_mapping(profile_spec)
    except Exception as exc:  # noqa: BLE001 - a malformed profile refuses before any spawn
        raise LauncherError(
            f"{STANDALONE_ADAPTER_REQUIRES_PROFILE}: {exc}") from exc
    return StandaloneRuntime(artifact_base=artifact_base, run_id=run_id,
                             profile=profile, runtime_state=runtime_state,
                             journal=journal,
                             worktree_path=profile.worktree or None)


def _standalone_pause_row_journal(artifact_base: Any, run_id: str) -> Any:
    """This run's PAUSE-ROW journal -- ``pause_store.FileSettlementJournal``, not the log.

    A different object from :func:`_standalone_journal_for` on purpose (external review
    #9): that one is the append-only `ExecutionJournal` the LifecycleSettlementPort reads,
    this one is the promotable one-row-per-intent store `executor`'s PAUSE and DISPOSE
    nodes write through ``row()``/``record()``.  Same run, same artifact base, two files,
    two interfaces.
    """
    from .pause_store import journal_for
    return journal_for(run_id, artifact_base=artifact_base)


def build_standalone_state(spec: dict[str, Any], adapter: Any) -> dict[str, Any]:
    """OS-37 D-2(b): the standalone runtime carries ONE capability declaration, not two.

    ``build_state`` above reads ``spec["capabilities"]``, so an operator's JSON and the
    adapter's own ``capabilities()`` are two independent declarations that nothing
    reconciles -- and ``validate_node`` gates on the FORMER while the ladder depends on the
    LATTER.  For a runtime born in this ticket that gap is closable at no cost, so the
    standalone path populates ``adapter_capabilities`` from the LIVE adapter.

    **The Orca and fake paths are deliberately NOT changed** -- D-2(a).  Reconciling them
    there is authorized by no acceptance criterion and would change routing for existing
    runs and for historical replay.  That residual is carried forward as PR-1, named, with a
    follow-up ticket proposed rather than silently fixed here.
    """
    return build_state({**spec, "capabilities": sorted(adapter.capabilities())})


#: The role spellings the engine uses -> the `task_context`/`dispatch_context` ones.
_STANDALONE_PROMPT_ROLE = {"WORKER": "worker", "PHASE_REVIEWER": "reviewer",
                          "FINAL_REVIEWER": "final_reviewer"}


def build_standalone_prompt_composer(*, objective: str,
                                     requested_phases: tuple[str, ...],
                                     risk: str = "high",
                                     project_root: Path | None = None,
                                     role_instructions: Mapping[str, str] | None = None
                                     ) -> Any:
    """The ONE renderer of the standalone production prompt -- follow-up review finding 5.

    Consolidated review finding 5.  `StandaloneAdapter.start` used to hand the agent the
    canonical `ActionIntent` JSON, because `ActionIntent` carries no prose and the
    standalone runtime has no prompt renderer -- so `run_workflow --adapter standalone`
    never delivered the role, phase, task contract, correction instruction or
    review-output contract a real CLI needs to produce a valid gate record.  On the Orca
    path that composition is `orca_runtime_harness.dispatch_context`, replayed into the
    dispatch preamble by the Orca skill environment; a headless CLI has NO such
    environment, so the standalone prompt must carry all of it itself.

    This closure is that renderer.  Given the `ActionIntent` the graph's EXECUTE_INTENT
    node hands `start`, it returns a SELF-CONTAINED prompt:

    * a role / phase / run narrative -- who the agent is and that this prompt is its whole
      instruction set;
    * the run OBJECTIVE (the task contract), the same string a `--adapter orca` launch
      passes as its objective;
    * the review-output contract -- the exact `STATUS:` / `RESULT:` line and the
      ``decision-gate`` record the workflow gate reads -- because the agent has no skill
      that would otherwise carry it;
    * on a CORRECTION or repair round, the correction instruction: read the reviewer's
      findings (from the shared worktree / the repair defects) and address them;
    * the SAME `dispatch_context` machine-control blocks the Orca path renders (task
      boundary, quality gate, risk profile, the generated decision-gate contract, and on a
      repair the validation-repair block), so ingress and egress cannot drift from the
      Orca path's.

    ``role_instructions`` maps a ``"<ROLE>:<ROUND_KIND>"`` key (e.g. ``"WORKER:PHASE_GATE"``)
    to an extra instruction the launch supplies as DATA -- how a run scopes a first
    iteration, say -- so a correction-loop demonstration's inducement rides the production
    boundary rather than being composed by a harness below it.  Empty by default: a bare
    production launch renders the production prompt and nothing else.
    """
    harness = _import_orca_runtime()
    from . import artifact_identity
    instructions = dict(role_instructions or {})
    # ---- round-7 consolidated review, follow-up item 7 -------------------------------
    # `project_root` used to be accepted and IGNORED: `dispatch_context` then fell back
    # to the harness's import-time `REPO_QUALITY_PROFILE`, i.e. the quality profile of
    # THIS repository, whatever project the run was pointed at.  The profile is now
    # resolved ONCE, here, under the given root -- the same `resolve_quality_profile`
    # the Orca path's `OrcaRuntimeHarness.__init__` / `start_run` use, defaulting to the
    # working directory exactly as `build_orca_adapter` does -- and an INVALID profile
    # is the same pre-dispatch refusal the Orca path raises (`INVALID_QUALITY_PROFILE`),
    # before any process exists.  An absent profile renders the absent block; a loaded
    # one renders its attributes into every role's quality gate block.
    quality = _import_quality_profile()
    root = Path(project_root) if project_root is not None else Path.cwd()
    quality_profile = quality.resolve_quality_profile(root)
    if quality_profile.is_invalid:
        raise LauncherError(
            f"{quality.INVALID_PROFILE_REASON}: {root / quality_profile.path} exists but is "
            f"not a valid quality profile ({quality_profile.error}); no standalone dispatch "
            "is composed and the generic checklist is not restored")

    def render(intent: Mapping[str, Any]) -> str:
        role = str(intent.get("role") or "WORKER")
        ctx_role = _STANDALONE_PROMPT_ROLE.get(role, "worker")
        phase = artifact_identity.contract_phase(role, str(intent.get("phase") or ""))
        gate_iteration = int(intent.get("gate_iteration") or 1)
        round_kind = str(intent.get("round_kind") or "PHASE_GATE")
        repair_instruction = intent.get("repair_instruction")
        mode = "complete" if role == "WORKER" else "pass"
        spec, _boundary, _reviewer_ctx = harness.dispatch_context(
            ctx_role, gate_iteration, mode, phase=phase, base_spec=objective,
            run_id=str(intent.get("run_id") or ""),
            quality_profile=quality_profile,
            requested_phases=tuple(p.lower() for p in requested_phases),
            risk=risk, risk_source="explicit",
            repair_instruction=repair_instruction)
        lines = [
            "=== STANDALONE AGENT DISPATCH ===",
            f"You are the {role} for the {phase} phase of run "
            f"{intent.get('run_id') or ''!s} (gate iteration {gate_iteration}, round "
            f"{round_kind}).",
            "You are a headless CLI agent with NO Orca skill environment loaded, so THIS "
            "prompt is your COMPLETE instruction set.  Do the work in the current working "
            "directory.",
            "",
        ]
        if role == "WORKER":
            if round_kind == "CORRECTION" or repair_instruction is not None:
                lines += [
                    "A reviewer read your previous submission against the task contract "
                    "and returned findings; they are recorded in REVIEW.md in the current "
                    "working directory (and, for a form repair, in the VALIDATION REPAIR "
                    "block below).  READ them and address every finding, then update your "
                    "work in place.",
                ]
            lines += [
                "When you are done, your FINAL message MUST contain, on their own lines, "
                "`STATUS: COMPLETE` and a `DECISION_GATE_STATE:` declaration, plus exactly "
                "one fenced ```decision-gate JSON record filled in per the contract below. "
                "Also write a short report to WORKER.md in the working directory.",
            ]
        else:
            lines += [
                "Review the worker's submission in the current working directory against "
                "the task contract.  Reach your verdict from your OWN reading of the files; "
                "do not assume a defect exists and do not assume the submission is correct.",
                "Your FINAL message MUST begin with `RESULT: PASS` or `RESULT: FAIL` on its "
                "own line, contain a `DECISION_GATE_STATE:` declaration and exactly one "
                "fenced ```decision-gate JSON record whose `verdict` is that same PASS/FAIL, "
                "filled in per the contract below.  Also write your findings to REVIEW.md "
                "in the working directory (one bullet per finding, or 'None').",
            ]
        supplement = instructions.get(f"{role}:{round_kind}") or instructions.get(role)
        if supplement:
            lines += ["", supplement]
        lines += ["", "--- TASK CONTRACT ---", objective, "--- END TASK CONTRACT ---",
                  "", spec]
        return "\n".join(lines)

    return render


def build_standalone_adapter(spec: dict[str, Any], *, artifact_base: Path,
                             run_id: str = "", runtime_state: Any = None,
                             profile_spec: Any = None,
                             approval_port: Any = None,
                             prompt_composition: Mapping[str, Any] | None = None
                             ) -> tuple[Any, dict[str, Any]]:
    """Compose the standalone adapter and the state it declares, in the fixed order.

    Composition order matches the Orca path's exactly -- journal, then ledger, then the
    adapter, then the state -- because the state is named after the run the ledger is keyed
    on, and deriving either from the other would name something that does not exist.

    ``approval_port`` is R4's conditional human-approval authority and defaults to ``None``,
    which is the pre-R4 composition unchanged: with no port the adapter declares no
    ``human_approval``, ``routing.pause_admissible`` refuses the route and a decision block
    still terminates as BLOCKED.  When an operator DOES name an authority
    (``--approval-authority artifact``) it is threaded in here, BEFORE the capability
    snapshot below, for exactly the reason external review #10 gives for the ledger: the
    state carries a frozen copy of ``adapter.capabilities()``, so a port attached after the
    snapshot would leave the run declaring a capability the adapter has and the state does
    not -- and ``routing`` reads the state.
    """
    from .standalone_adapter import StandaloneAdapter
    from .standalone_journal import ExecutionJournal

    resolved_run = run_id or spec.get("run_id") or "run_standalone"
    if profile_spec is None:
        raise LauncherError(
            f"{STANDALONE_ADAPTER_REQUIRES_PROFILE}: --adapter standalone needs an explicit "
            "driver profile; there is deliberately no built-in CLI table (AC-37-03)")
    journal = ExecutionJournal(artifact_base, resolved_run)
    # ---- Final-Review iteration 5, B1: the launch-time worktree is FROZEN here --------
    # The ONE launch composition door.  A relative `worktree` / `add_dirs` entry in the
    # operator's profile means "relative to where I launch from", and that meaning is
    # fixed HERE, in the launching process, before the spec is digested (the create-once
    # authority binding below), exact-match checked, archived (`publish_launch_bindings`)
    # and handed to the runtime -- so the archive a Watchdog started from ANY cwd rebuilds
    # from holds the launch-time absolute path, and the digest names the worktree the run
    # really executes in (see `freeze_profile_worktree` for why hashing the frozen
    # mapping is the safe choice: same cwd -> exact-match restart; another cwd -> a
    # different worktree -> STANDALONE_AUTHORITY_CONFLICT, never a silent re-bind).
    profile_spec = freeze_profile_worktree(profile_spec)
    # ---- OS-37 correction R3: the DECLARED worktree reaches the child -----------------
    # `StandaloneSession.worktree_path` defaults to `os.getcwd()`, and this composition
    # root never overrode it -- so every agent a shipped `run_workflow.py --adapter
    # standalone` launched ran in the LAUNCHER's working directory, whatever worktree the
    # profile declared.  It went unnoticed because a driver whose argv template happens to
    # carry the worktree still put the agent in the right place; a driver with no such
    # template simply inherited the launcher's cwd.  (Which drivers those are is the DRIVER
    # LAYER's business and is deliberately not named here -- D4.1.)  The defect was
    # invisible until the R10 real-CLI
    # evidence was re-driven through this function, which is exactly why R3 requires it to
    # be.
    #
    # Round-8 item 3: a profile that declares NO worktree no longer reaches the runtime
    # with an empty one -- `freeze_profile_worktree` above bound it to this launching
    # process's absolute cwd, so the session's `os.getcwd()` fallback is never what a
    # RECOVERING process reads.  (`build_standalone_runtime` keeps `or None` only for a
    # direct in-process caller that never persists.)
    runtime = build_standalone_runtime(artifact_base, resolved_run,
                                       runtime_state=runtime_state,
                                       profile_spec=profile_spec, journal=journal)
    # Finding 5 (round 4) / findings 5 and 8 (follow-up) / CORRECTION 2.  The profile and
    # the launch binding (ledger path, thread, approval authority) are what a Watchdog
    # rebuilds the runtime from, and the binding is create-once / exact-match.  They are
    # NOT written here any more.  Composition happens before `execute_state` claims the
    # run-scoped execution authority, so a first-writer race existed: while another
    # Coordinator held the lease and no binding existed yet, an invocation that was about
    # to be refused `EXECUTION_AUTHORITY_HELD` could still CREATE the binding, and the
    # create-once rule then made that unauthorised binding permanent.  What happens here
    # is READ-ONLY: a pre-existing DIFFERENT binding is refused by name now, before any
    # claim and before any process (the exact-match refusal is unchanged); the WRITE is
    # `publish_launch_bindings`, which `execute_state` calls immediately after a
    # SUCCESSFUL claim and before any spawn, and a refused claim never reaches.
    ledger_path = getattr(runtime_state, "path", None)
    # Round-7 blocker 1: the EFFECTIVE thread identity -- the same resolver `build_state`
    # binds into the state (and `run_cli` keys the default ledger on) -- so a launch that
    # omits `thread_id` records ONE identity everywhere, never `""` in the authority and
    # `"launcher"` in the state and ledger.  Asserted structurally against the state
    # built below.
    thread_id = str(effective_thread_id(spec))
    approval_authority = approval_authority_name(approval_port)
    # Blocker 2: the prompt composition is DATA at this boundary -- the record that is
    # persisted at launch and bound (by digest) into the authority -- and the renderer is
    # derived from it by the one function a recovery also derives it from.  ``None``
    # declares `composer: none` (the canonical-intent payload of the scripted paths).
    composition = (dict(prompt_composition) if prompt_composition is not None
                   else prompt_composition_record(None))
    composer = composer_from_composition(composition)
    if ledger_path is not None:
        # Finding 2: the pre-claim exact-match check binds the profile digest too, so a
        # relaunch of the same run/thread with a DIFFERENT profile is refused before any
        # claim -- the create-once binding covers the profile, not only the ledger.
        check_standalone_authority(artifact_base, resolved_run,
                                   runtime_state_path=ledger_path, thread_id=thread_id,
                                   approval_authority=approval_authority,
                                   profile_digest=profile_digest(profile_spec),
                                   prompt_composition_digest=prompt_composition_digest(
                                       composition))
    adapter = StandaloneAdapter(runtime, runtime_state=runtime_state,
                                settlement_journal=journal,
                                pause_row_journal=_standalone_pause_row_journal(
                                    artifact_base, resolved_run),
                                approval_port=approval_port,
                                artifact_base=artifact_base, run_id=resolved_run,
                                # Finding 5: the production prompt renderer, when the
                                # composition root supplies one.  ``None`` keeps the
                                # canonical-intent payload for the scripted/fake paths.
                                prompt_composer=composer)
    # The post-claim publication step (CORRECTION 2), bound to THIS composition's facts
    # and attached to the adapter so `execute_state` -- which is adapter-neutral and reads
    # it by name -- can run it once the run is really this process's to launch.
    adapter.publish_launch_bindings = functools.partial(
        publish_standalone_launch_bindings, artifact_base, resolved_run,
        profile_spec=profile_spec, runtime_state_path=ledger_path,
        thread_id=thread_id, approval_authority=approval_authority,
        prompt_composition=composition)
    # ---- OS-37 external review #10: the capability SNAPSHOT comes LAST ----------------
    # `build_standalone_state` reads `adapter.capabilities()`, and `external_resume` is
    # declared only when the identity fence has an authority to live in -- i.e. only once
    # `runtime_state` is attached.  `run_cli` used to build the adapter here, build the
    # state from it, and only THEN construct the durable ledger, so the snapshot was taken
    # with `runtime_state=None` and the run permanently declared no `external_resume`:
    # `executor._collect` then refused every post-receipt recovery with
    # IDEMPOTENCY_RECOVERY_UNSUPPORTED, making a crash after the receipt unrecoverable.
    # The ledger is now threaded in BEFORE the snapshot, and this refusal makes the
    # ordering structural rather than a comment.
    if runtime_state is None:
        raise LauncherError(
            f"{STANDALONE_ADAPTER_REQUIRES_LEDGER}: the durable runtime-state ledger must "
            "be constructed BEFORE the standalone adapter, because the capability "
            "declaration this state carries is snapshotted from the live adapter and "
            "external_resume is withdrawn while the identity fence has no ledger to live in")
    state = build_standalone_state({**spec, "run_id": resolved_run}, adapter)
    if state["thread_id"] != thread_id:
        raise LauncherError(
            f"{STANDALONE_ADAPTER_REQUIRES_LEDGER}: the state binds thread "
            f"{state['thread_id']!r} but the launch authority would record {thread_id!r}; "
            "one launch has exactly one thread identity")
    return adapter, state


# ---- OS-43 CRITICAL: the Coordinator's half of the run-scoped execution authority -----
def _execution_authority(checkpointer: Any) -> tuple[Any, str]:
    """The authority guarding THIS run's checkpoint store, or ``(None, "")``.

    Keyed on the checkpoint store rather than on the run id, because the thing being
    serialised is "who may advance THIS checkpoint", and for the canonical run-rooted
    layout ``recovery_store.authority_path_for_checkpoint`` resolves to exactly the
    record ``recovery_runtime._recover_active`` claims -- one file, one lock, one owner.

    ``None`` when the graph has no durable checkpoint store at all.  Such a run is not
    addressable by any other process: it publishes no checkpoint for a Watchdog to
    discover, so there is no second claimant for it to be serialised against.  Production
    never takes this branch -- ``build_graph`` refuses a non-durable checkpointer unless
    the caller explicitly asks for the named test-only escape hatch.
    """
    path = getattr(checkpointer, "path", None)
    if path is None:
        return None, ""
    try:
        return recovery_store.FileRecoveryStateStore(
            recovery_store.authority_path_for_checkpoint(path)), ""
    except recovery_store.RecoveryStoreLockUnavailable:  # pragma: no cover - non-POSIX
        # No inter-process lock means no exclusive claim is possible.  Reported as an
        # absent authority rather than as a claim that cannot be enforced.
        return None, ""


def _authority_keeper(authority: Any, run_id: str, lease_token: str) -> Any:
    """Renew the authority for the whole run, so a healthy owner is never taken over.

    The same ``LeaseKeeper`` the executor and ``coordinator_liveness`` already use,
    unmodified: a lease that is never renewed is exclusive for one lease period, which is
    far shorter than a workflow.
    """
    if authority is None:
        return None
    from .lease_keeper import LeaseKeeper, heartbeat_interval_for
    return LeaseKeeper(
        authority, run_id, lease_token,
        interval_seconds=heartbeat_interval_for(authority.lease_seconds)).start()


def _authority_now() -> str:
    from .turn_boundary import authority_now
    return authority_now()


def _authority_blocked(raw_state: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    """Stop this Coordinator, closed, with a named reason and no external effect.

    Exactly the shape ``RuntimeStateConflict`` already produces below: the run terminates
    BLOCKED (exit code 1) rather than crashing, and -- because this happens before
    ``graph.invoke`` -- it performs nothing at all.
    """
    blocked = dict(raw_state)
    blocked["route_token"] = "BLOCK"
    blocked["terminal_reason"] = {"code": code, "message": message}
    return terminal_node(blocked)


# ---- OS-43 F-001: FINISHED and INTERRUPTED are different durable facts ----------------
# The authority made the two parties exclusive WHILE one of them runs, and nothing more:
# a Coordinator that finished released an `ACTIVE` record, its lease then lapsed, and the
# next `execute_state` over the same run found a takeable record, minted a fresh
# claimant, rotated the token and drove the graph again.  R6 forbids exactly that -- a
# retry, a process restart or a replayed message may not change the winner and may not
# perform a second transition -- so terminal completion has to become DURABLE, not merely
# unheld.
#
# The rule itself, the FINISHED / INTERRUPTED / PAUSED / UNREADABLE table and the reasons
# behind each row live in ONE place -- `turn_boundary.settle_or_release`, beside the
# `classify_checkpoint_state` the whole decision rests on -- because the Watchdog closes
# its own hold through the SAME function.  Two parallel implementations of this are what
# the last two review gates each failed on, in mirror image; there is now one.
def _committed_terminal_state(checkpointer: Any, thread_id: str) -> dict[str, Any] | None:
    from .turn_boundary import committed_terminal_state
    return committed_terminal_state(checkpointer, thread_id)


def _settle_or_release(authority: Any, run_id: str, lease_token: str, checkpointer: Any,
                       thread_id: str) -> str:
    from .turn_boundary import settle_or_release
    return settle_or_release(authority, run_id, lease_token, checkpointer=checkpointer,
                             thread_id=thread_id)


def execute_state(raw_state: dict[str, Any], *, adapter: Any, checkpointer: Any = None,
                  runtime_state: Any = None, recursion_limit: int | None = None,
                  thread_id: str | None = None, interrupt_before: list[str] | None = None,
                  interrupt_after: list[str] | None = None,
                  checkpoint_store_path: str | Path | None = None,
                  artifact_base: Path | None = None,
                  require_durable_checkpointer: bool = True,
                  execution_authority: Any = None,
                  **graph_options: Any) -> dict[str, Any]:
    """Run the compiled graph to a terminal state, failing closed on malformed input.

    This validates the raw mapping before invoking, so the process entry point reports the
    precise ``StateError`` for any malformed input, not just the unknown-field case.  The
    compiled graph boundary (``GuardedWorkflowGraph``) enforces the closed field set
    independently, so a caller that bypasses this function still fails closed.

    A durable ``RuntimeStatePort`` is required, here as everywhere: it is resolved (and
    refused if absent) before the state is even inspected.  ``run_cli`` supplies one by
    default, so the shipped command line is crash-safe without extra flags.

    ``execution_authority`` is a PORT INSTANCE for test injection -- the run-scoped claim
    authority, the same role ``recover_stalled_run``'s ``store`` plays
    (``recovery_runtime.py:372-380``).  It is not a decision and cannot express one: the
    only thing a caller can vary is WHICH record (and which process identity) the claim is
    taken against, never whether one is taken.  Production leaves it ``None`` and the
    authority is derived from the run's own durable checkpoint store.
    """
    from .graph import build_graph

    resolve_runtime_state(adapter, runtime_state)
    try:
        validate_state(dict(raw_state), expected_thread_id=raw_state.get("thread_id", ""))
    except (StateError, TypeError, ValueError, KeyError, AttributeError) as exc:
        return terminal_node(normalize_malformed_state(
            raw_state, code="MALFORMED_STATE", message=str(exc)))

    if checkpointer is None and require_durable_checkpointer:
        # Durable by default, exactly as the ledger already is: a shipped command line that
        # can pause must be able to survive the process with no extra flags.
        from .checkpoint_store import FileCheckpointSaver
        checkpointer = FileCheckpointSaver(resolve_checkpoint_path(
            raw_state["run_id"], thread_id or raw_state["thread_id"],
            explicit=checkpoint_store_path, artifact_base=artifact_base))
    # ---- OS-43 CRITICAL: the run-scoped EXECUTION AUTHORITY -------------------------
    # This is the Coordinator's ordinary path to ``graph.invoke``, and before this it took
    # NO run-scoped authority: the recovery lease serialised Watchdog-vs-Watchdog and the
    # Coordinator never participated, so a Coordinator that revived after a Watchdog had
    # observed it stale could drive the same checkpoint concurrently.  Both now pass
    # through ``recovery_store.claim``, and whichever gets there first owns the run.
    #
    # ``takeover=False``: this claimant has a run in hand and cannot observe.  A live
    # holder is a final refusal, reported as BLOCKED with a stable code -- it neither
    # waits nor proceeds.
    authority, authority_token = ((execution_authority, "")
                                  if execution_authority is not None
                                  else _execution_authority(checkpointer))
    if authority is not None:
        try:
            claimed = authority.claim(
                raw_state["run_id"],
                thread_id=thread_id or raw_state["thread_id"], checkpoint_ns="",
                now_iso=_authority_now(),
                owner_kind=recovery_store.OWNER_KIND_COORDINATOR, takeover=False)
        except recovery_store.RecoveryAuthorityHeld as exc:
            # ``takeover=False`` makes this the ONLY held-claim exception reachable here,
            # which is exactly the point: a Coordinator has no observe branch to fall into.
            return _authority_blocked(raw_state,
                                      recovery_store.EXECUTION_AUTHORITY_HELD, str(exc))
        except recovery_store.RecoveryRecordCorrupt as exc:
            return _authority_blocked(raw_state, "RECOVERY_RECORD_CORRUPT", str(exc))
        if claimed["claim_outcome"] == recovery_store.ALREADY_SETTLED:
            # R6.  This run FINISHED and its authority records that, so a restart owes the
            # caller the outcome the run already reached -- read back from the committed
            # checkpoint -- and no second transition.  `claim` returns ALREADY_SETTLED
            # WITHOUT touching the record, so the winner's `claimant_id` and `lease_token`
            # are still the winner's after this returns.  Returning before `audit_sink` is
            # resolved is deliberate: a restart publishes no audit record either.
            settled = _committed_terminal_state(checkpointer,
                                                thread_id or raw_state["thread_id"])
            if settled is not None:
                return settled
            # SETTLED with no readable terminal head: something sealed a run whose outcome
            # this process cannot read, so it refuses rather than inventing one.
            return _authority_blocked(
                raw_state, recovery_store.EXECUTION_AUTHORITY_HELD,
                f"{raw_state['run_id']}: this run's execution authority is SETTLED")
        authority_token = claimed["lease_token"]
        # R4.  Carried into the graph so EXECUTE_INTENT / PAUSE / DISPOSE revalidate the
        # token before every external effect, and checked once more immediately before the
        # transition below.
        graph_options.setdefault(
            "execution_fence",
            lambda: authority.fence(raw_state["run_id"], authority_token))
    # EVERYTHING from here to the `finally` runs while this Coordinator HOLDS the run's
    # execution authority, so everything from here is inside the `try`: building the graph
    # or starting the lease keeper can raise, and a raise outside the held section would
    # leave the record ACTIVE with nothing closing it -- the same class of leak as an
    # unsettled completion, one step earlier.
    keeper = None
    try:
        # ---- CORRECTION 2 (follow-up review finding 5): publish the launch bindings ---
        # HERE -- the claim above succeeded, so this process is the run's launcher; a
        # refused claim returned before this line and wrote nothing -- and BEFORE the graph
        # is built, so no spawn can precede the record a Watchdog rebuilds from.  Read by
        # name: the standalone composition attaches it, the Orca and fake adapters have no
        # such attribute and are untouched.  A refusal raised here (a conflicting binding)
        # leaves through the `finally` below, which releases the authority just claimed.
        publish = getattr(adapter, "publish_launch_bindings", None)
        if callable(publish):
            publish()
        if "audit_sink" not in graph_options:
            # OS-42.  The run's own append-only ORCHESTRATOR_LOG.md is where the
            # validation-repair audit trail lands, and this is the entry point that knows
            # where that run root is.  Passing `audit_sink=None` explicitly still disables
            # it, which is what every in-process test does so a suite run writes no
            # artifacts.
            from .audit import RunLoggingAuditSink
            graph_options["audit_sink"] = RunLoggingAuditSink(
                raw_state["run_id"], artifact_base=artifact_base)
        config: dict[str, Any] = {
            "recursion_limit": recursion_limit or default_recursion_limit(raw_state)}
        # Unconditional: a thread id is what makes a run addressable by a successor.
        config["configurable"] = {"thread_id": thread_id or raw_state["thread_id"],
                                  "checkpoint_ns": ""}
        graph = build_graph(adapter, checkpointer=checkpointer,
                            runtime_state=runtime_state,
                            interrupt_before=interrupt_before,
                            interrupt_after=interrupt_after,
                            require_durable_checkpointer=require_durable_checkpointer,
                            **graph_options)
        keeper = _authority_keeper(authority, raw_state["run_id"], authority_token)
        if authority is not None:
            # Immediately before the transition, atomically.  A token that no longer
            # matches means a successor already owns this run.
            authority.fence(raw_state["run_id"], authority_token)
        final = graph.invoke(raw_state, config)
        # ---- follow-up review finding 2: the pause record is written HELD ----------
        # The Tier-2 record used to be written after the `finally` below had let the
        # execution authority go, and a record that could not be written left the
        # committed head an orphaned WAITING_FOR_INPUT under a CLI that said BLOCKED.
        # Writing it here -- still the holder -- lets a failed write COMMIT the same
        # BLOCKED terminal it reports, through the graph's own typed update, and lets
        # `_settle_or_release` read that head and SETTLE the authority exactly as it
        # would for any run that ended.  The pause store's own claim is a different
        # authority from the execution lease (it serialises resumers, not executors),
        # so holding one while taking the other claims nothing this caller lacks.
        final = _finalize_pause_if_waiting(final, checkpointer=checkpointer,
                                           artifact_base=artifact_base,
                                           graph=graph, config=config)
    except recovery_store.RecoveryClaimLost as exc:
        # The fence refused -- at the transition or inside a node, before its effect.  The
        # run stops with a named reason; it does not finish work a successor now owns.
        return _authority_blocked(raw_state, "EXECUTION_AUTHORITY_LOST", str(exc))
    except RuntimeStateConflict as exc:
        # A corrupt, incompatible or contended durable ledger stops the run *before* any
        # further external effect, and is reported as BLOCKED rather than as a crash.  It is
        # never silently treated as an empty ledger, which is what allowed every effect to be
        # recreated.
        blocked = dict(raw_state)
        blocked["route_token"] = "BLOCK"
        blocked["terminal_reason"] = {"code": runtime_state_error_code(exc), "message": str(exc)}
        return terminal_node(blocked)
    except IdempotencyRecoveryError as exc:
        # An unreconcilable crash window is a terminal BLOCKED outcome, not a crash: the run
        # stops with a named reason instead of re-creating an external effect it cannot prove
        # is absent.  Exit code 1 distinguishes it from a completed or escalated run.
        blocked = dict(raw_state)
        blocked["route_token"] = "BLOCK"
        blocked["terminal_reason"] = {"code": exc.code, "message": exc.detail}
        return terminal_node(blocked)
    finally:
        # The authority is closed on every exit path -- success, refusal and crash alike
        # -- so a Coordinator that stops holds nothing.  WHICH way it is closed is decided
        # by the run's own committed head and nothing else (`_settle_or_release`): a run
        # that FINISHED is SETTLED, and one that stopped short of finishing is released,
        # so it becomes legitimately recoverable again the instant this owner lets go.
        if keeper is not None:
            keeper.stop()
        if authority is not None:
            _settle_or_release(authority, raw_state["run_id"], authority_token,
                               checkpointer, thread_id or raw_state["thread_id"])
    # OS-42.  A settled run executes no further node, so the outbox needs one retry point
    # after the graph has stopped -- this is it.  Outside the lifecycle path by
    # construction: it reads the final state, delivers, and writes nothing back, so it can
    # neither raise into the run nor change what the run decided.
    from .audit import drain
    drain(graph_options.get("audit_sink"), final)
    return final


#: The named refusal a pause that cannot be RECORDED reports.  It is a member of
#: `pause_policy.PAUSE_REFUSAL_CODES`, so `terminal_node` already prints it as the reason a
#: run BLOCKED rather than folding it into an ordinary decision block.
PAUSE_RECORD_NOT_WRITTEN = "PAUSE_RECORD_MISSING"


def _finalize_pause_if_waiting(final: dict[str, Any], *, checkpointer: Any,
                               artifact_base: Path | None, graph: Any = None,
                               config: Any = None) -> dict[str, Any]:
    """Write the Tier-2 pause record after ``invoke`` returned.  R4's third wiring.

    ``pause_runtime.finalize_pause`` had NO production caller: `resume_run` calls it for a
    re-pause and the OS-31 fixtures call it themselves, but the ordinary graph entry point
    -- this one, the one `run_workflow.py` uses -- never did.  A run that paused therefore
    reached ``WAITING_FOR_INPUT``, published its clarification request and committed its
    checkpoint, and then left NO durable pause record at all: `discover` could not list it,
    `resume` refused it with `PAUSE_RECORD_MISSING`, and the pause was unrecoverable.  That
    is the lifecycle-journal defect R4 suspected the missing approval port was masking, and
    it was invisible for exactly that reason -- with no approval capability the PAUSE node
    was unreachable, so nothing ever got far enough to notice.

    Called INSIDE the held execution-authority section (follow-up review finding 2) --
    the record is the pause's own durable authority, claimed through `pause_store`, and
    taking it while still holding the execution lease claims nothing this caller lacks;
    what it buys is that a failed write can still COMMIT a terminal to the run's own
    checkpoint and have `_settle_or_release` seal the authority over it.

    **Fail-closed, durably.**  A pause that cannot be recorded must not be REPORTED as a
    pause: nothing could ever resume it, and exit code 4 would tell an operator to wait
    for a human on a run no `discover` will ever list.  The refusal is converted into the
    ordinary BLOCKED terminal, named, exactly as `pause_node`'s own refusals are -- AND
    that terminal is written to the committed head through the graph's typed
    `PAUSE_NOT_RECORDED` update, so the durable statement and the reported one are the
    same statement.  A head that cannot be re-committed either is reported as BLOCKED
    with BOTH failures named; it is never reported as a pause.

    ``artifact_base is None`` means this caller named no run root, so there is nowhere a
    pause record belongs; the state is returned untouched, which is what every in-process
    test that drives the graph without an artifact tree already relies on.
    """
    if final.get("run_lifecycle") != "WAITING_FOR_INPUT" or artifact_base is None:
        return final
    from . import pause_runtime, pause_store
    store_path = getattr(checkpointer, "path", None)
    if store_path is None:
        return _pause_not_recorded(
            final, "the checkpointer names no durable store path, so a pause record "
                   "would name a checkpoint store nothing can reopen")
    try:
        pause_runtime.finalize_pause(
            final, saver=checkpointer,
            store=pause_store.store_for(final["run_id"], artifact_base=artifact_base),
            checkpoint_store_path=str(store_path), artifact_base=artifact_base)
    except pause_runtime.PauseRefused as exc:
        return _pause_not_recorded(final, str(exc), code=exc.code, graph=graph,
                                   config=config)
    except (OSError, ValueError, KeyError) as exc:  # noqa: BLE001 - unrecorded is refused
        return _pause_not_recorded(final, f"{type(exc).__name__}: {exc}", graph=graph,
                                   config=config)
    return final


def _pause_not_recorded(final: dict[str, Any], detail: str,
                        code: str = PAUSE_RECORD_NOT_WRITTEN, *, graph: Any = None,
                        config: Any = None) -> dict[str, Any]:
    """Turn an unrecordable pause into the BLOCKED terminal, with the reason named --
    and COMMIT it (finding 2), so the durable head is not an orphaned pause.

    The commit goes THROUGH THE GRAPH'S OWN NODES, not around them: the paused head is
    re-invoked on the same thread with ``route_token=BLOCK`` and the refusal as its
    ``terminal_reason``, so VALIDATE judges the state, ROUTE short-circuits to the
    recorded token exactly as it does for every terminal reason, and TERMINAL writes the
    BLOCKED terminal -- naming the refusal, because `PAUSE_RECORD_MISSING` is a member of
    `pause_policy.PAUSE_REFUSAL_CODES` and TERMINAL keeps those by name.  No raw
    checkpoint write, no typed out-of-band command and no edit to any pinned policy
    module: the same route a refused pause takes inside the graph.  The returned state is
    the COMMITTED one, so what is reported is what is durable.

    A head that cannot be re-committed either is reported BLOCKED with BOTH failures
    named and ``head_committed=False``; it is never reported as a pause.
    """
    blocked = dict(final)
    blocked["run_lifecycle"] = "ACTIVE"
    blocked["route_token"] = "BLOCK"
    blocked["terminal_reason"] = {"code": code, "message": detail}
    terminal = terminal_node(blocked)
    if graph is None or config is None:
        return terminal
    reentry = dict(final)
    reentry["route_token"] = "BLOCK"
    reentry["terminal_reason"] = {"code": code, "message": detail,
                                  "phase": final.get("current_phase")}
    try:
        committed = graph.invoke(reentry, config)
    except Exception as exc:  # noqa: BLE001 - both failures are NAMED, neither is a pause
        terminal = dict(terminal)
        terminal["terminal_reason"] = {
            **terminal["terminal_reason"],
            "message": f"{detail}; and the BLOCKED terminal could not be committed to "
                       f"the checkpoint head ({type(exc).__name__}: {exc}); the durable "
                       "head may still read WAITING_FOR_INPUT with no pause record",
            "head_committed": False}
        return terminal
    if committed.get("terminal_status") != "BLOCKED":
        terminal = dict(terminal)
        terminal["terminal_reason"] = {
            **terminal["terminal_reason"],
            "message": f"{detail}; and the re-entry committed "
                       f"{committed.get('terminal_status')!r} rather than BLOCKED",
            "head_committed": False}
        return terminal
    return dict(committed)


# ---- OS-42 F-002: the production Orca execution path ---------------------------------
# Before this, the shipped launcher offered `--adapter fake` and nothing else, so the
# bounded validation-repair loop could only ever run against scripted settlements. The
# feature could not reach the path where OS-42 actually failed.
#
# `OrcaAdapter` itself was always installed and takes its runtime by injection, so what
# was missing was (a) a way to SELECT it from the shipped command line and (b) the
# runtime to hand it. `release_manifest.ORCA_RUNTIME_CLOSURE` now installs that runtime
# beside the engine, which is why the import below resolves inside the installed package
# and no longer reaches for a repository-only module.

FAKE_ADAPTER = "fake"
ORCA_ADAPTER = "orca"
# OS-37 WI-13.  The standalone runtime owns local agent processes itself and needs no Orca
# process, binary, API or terminal lifecycle.  C-DESIGN-1: `--adapter` is declared at THREE
# argparse sites over this one shared tuple, and dispatched at four more, so adding a member
# here widens seven surfaces at once.  Every one of those seven is enumerated in DESIGN
# D1.2, and the arms where `standalone` is meaningless are EXPLICIT REFUSALS rather than
# fall-throughs -- a silent fall-through would compose a fake adapter under a standalone
# flag, which is the worst possible reading of an operator's intent.
STANDALONE_ADAPTER = "standalone"
ADAPTERS = (FAKE_ADAPTER, ORCA_ADAPTER, STANDALONE_ADAPTER)

# Refusals the standalone path raises BEFORE any process exists.
STANDALONE_ADAPTER_REQUIRES_STATE = "STANDALONE_ADAPTER_REQUIRES_STATE"
STANDALONE_ADAPTER_REQUIRES_PROFILE = "STANDALONE_ADAPTER_REQUIRES_PROFILE"
#: Retired by the follow-up review's finding 2: the `resume` verb now COMPOSES the
#: standalone runtime from the run's recorded launch binding instead of refusing it, and
#: a foreign adapter on a standalone run is `STANDALONE_RUN_ADAPTER_MISMATCH`.  The name
#: is kept so the conformance record's history still resolves; no site composes it.
STANDALONE_ADAPTER_UNSUPPORTED_HERE = "STANDALONE_ADAPTER_UNSUPPORTED_HERE"
#: OS-37 external review #10.  The capability declaration the standalone state carries is a
#: SNAPSHOT of the live adapter, and `external_resume` is withdrawn while the identity fence
#: has no ledger to live in -- so composing the adapter before the ledger silently produced
#: a run that could never collect an effect an earlier process created.  Refused by name.
STANDALONE_ADAPTER_REQUIRES_LEDGER = "STANDALONE_ADAPTER_REQUIRES_LEDGER"

# ---- OS-37 correction R4: the CONFIGURED human-approval authority --------------------
# `routing.pause_admissible` requires BOTH `human_approval` and `lifecycle_settlement`, and
# the standalone composition declared only the second, so the graph's own PAUSE node was
# unreachable for `--adapter standalone` -- a decision block terminated the run as BLOCKED
# instead of becoming a durable, resumable pause.
#
# The fix is a WIRING, not a policy change, and it is deliberately CONDITIONAL.  Declaring
# `human_approval` unconditionally would assert of every standalone run that a human
# authority exists to answer it, which is exactly the dishonest declaration
# `StandaloneAdapter.capabilities` refuses to make for `external_resume` on a wiring that
# cannot back it.  So the authority is named by the operator, once, at the composition
# root, and `NO_APPROVAL_AUTHORITY` is the default: a run launched without it composes
# byte-for-byte the adapter it composed before, declares no `human_approval`, and still
# routes a decision block to BLOCK.
#
# `ARTIFACT_APPROVAL_AUTHORITY` names the real OS-30 `ArtifactHumanApprovalPort` over the
# run's own artifact base -- the SAME authority `run_pause_cli` and `_watchdog_wiring`
# already resolve through `_artifact_approval_port`, so a run that pauses is answerable by
# the `discover`/`resume` verbs that already ship.  There is no third member and no
# stand-in: an authority that cannot really publish, show and ingest a decision is not one.
NO_APPROVAL_AUTHORITY = "none"
ARTIFACT_APPROVAL_AUTHORITY = "artifact"
APPROVAL_AUTHORITIES = (NO_APPROVAL_AUTHORITY, ARTIFACT_APPROVAL_AUTHORITY)
UNKNOWN_APPROVAL_AUTHORITY = "UNKNOWN_APPROVAL_AUTHORITY"

# Refusals the production path raises BEFORE any Orca effect exists.
ORCA_ADAPTER_REQUIRES_STATE = "ORCA_ADAPTER_REQUIRES_STATE"
ORCA_ADAPTER_REQUIRES_OBJECTIVE = "ORCA_ADAPTER_REQUIRES_OBJECTIVE"
ORCA_ADAPTER_REQUIRES_AGENT_PROFILE = "ORCA_ADAPTER_REQUIRES_AGENT_PROFILE"
ORCA_RUNTIME_UNAVAILABLE = "ORCA_RUNTIME_UNAVAILABLE"


def _skill_md_path() -> Path:
    """The orchestration SKILL.md this engine belongs to, in either layout."""
    return _import_orca_runtime().SKILL_MD_PATH


def _import_orca_runtime() -> Any:
    """`orca_runtime_harness`, imported lazily and from either layout.

    Lazy for the same reason `orca_adapter._default_result_parser` is: this module is
    inside the shipped engine package and the harness is a `tools/` sibling, so a
    module-scope import would make the whole package unimportable in an installation
    that carries only the engine.
    """
    try:  # repository layout
        from scripts import orca_runtime_harness
    except ImportError:  # pragma: no cover - flat installed Skill layout
        import orca_runtime_harness  # type: ignore[no-redef]
    return orca_runtime_harness


def _import_quality_profile() -> Any:
    """The quality-profile resolver, from the repository or the installed Skill layout."""
    try:
        from scripts import quality_profile
    except ImportError:  # installed Skill layout exposes sibling tools directly
        import quality_profile  # type: ignore[no-redef]
    return quality_profile


def _import_agent_profile() -> Any:
    try:  # repository layout
        from scripts import agent_profile
    except ImportError:  # pragma: no cover - flat installed Skill layout
        import agent_profile  # type: ignore[no-redef]
    return agent_profile


def _import_skill_policy() -> Any:
    try:  # repository layout
        from scripts import skill_policy
    except ImportError:  # pragma: no cover - flat installed Skill layout
        import skill_policy  # type: ignore[no-redef]
    return skill_policy


def orca_run_routing(*, agent_profile_name: str, requested_phases: tuple[str, ...],
                     risk: str, project_root: Path) -> Any:
    """Materialize the run's agent routing, or refuse.

    A routing is REQUIRED on this path and is not defaulted. Without one the harness
    falls back to its repository-local fake-agent shim, which does not exist in an
    installed tree -- so an installed run with no routing would create a terminal that
    can never settle. Refusing here means no Task, no Dispatch and no terminal is made.
    """
    agent_profile = _import_agent_profile()
    if not agent_profile_name:
        raise LauncherError(
            f"{ORCA_ADAPTER_REQUIRES_AGENT_PROFILE}: --adapter orca dispatches real "
            "agents and needs --agent-profile <name>; there is no default agent")
    selection = agent_profile.select_agent_profile(
        agent_profile_name, project_root=project_root)
    if not selection.is_selected:
        raise LauncherError(
            f"{ORCA_ADAPTER_REQUIRES_AGENT_PROFILE}: agent profile "
            f"{agent_profile_name!r} did not resolve ({selection.reason})")
    # The agent-command trust boundary, in the order `skill_policy._resolve_agent_routing`
    # applies it: whole-definition token/allowlist safety first, then availability for
    # the entries this run will actually dispatch. Both read the SAME policy contract the
    # Coordinator reads, so the shipped launcher cannot be a weaker door into the same
    # runtime than the documented one.
    skill_policy = _import_skill_policy()
    contract = skill_policy.load_policy_contract(_skill_md_path())
    known_commands = set(contract["known_agent_commands"])
    custom_pattern = re.compile(str(contract["custom_agent_command_pattern"]), re.ASCII)
    try:
        agent_profile.validate_profile_command_safety(
            selection.profile, token_pattern=skill_policy.AGENT_COMMAND_PATTERN,
            known_commands=known_commands, custom_command_pattern=custom_pattern)
    except agent_profile.AgentProfileError as exc:
        raise LauncherError(
            f"{ORCA_ADAPTER_REQUIRES_AGENT_PROFILE}: {exc.reason}: {exc}") from exc
    routing = agent_profile.materialize_run_routing(
        runtime="orchestration", selection=selection,
        requested_phases=requested_phases, risk=risk)
    try:
        agent_profile.validate_routing_commands(
            routing, token_pattern=skill_policy.AGENT_COMMAND_PATTERN,
            known_commands=known_commands, custom_command_pattern=custom_pattern)
    except agent_profile.AgentProfileError as exc:
        raise LauncherError(
            f"{ORCA_ADAPTER_REQUIRES_AGENT_PROFILE}: {exc.reason}: {exc}") from exc
    unresolved = routing.unresolved_required()
    if unresolved:
        raise LauncherError(
            f"{ORCA_ADAPTER_REQUIRES_AGENT_PROFILE}: profile {agent_profile_name!r} "
            "leaves required roles unrouted: "
            + ", ".join(f"{entry.phase}/{entry.role}" for entry in unresolved))
    return routing


def build_orca_adapter(spec: dict[str, Any], *, objective: str, artifact_base: Path,
                       runtime_state: Any = None, agent_profile_name: str = "",
                       project_root: Path | None = None,
                       harness_factory: Any = None) -> tuple[Any, dict[str, Any]]:
    """Create the Orca Run and return ``(adapter, state)`` bound to it.

    The state is built AFTER the Run exists and carries the Orca Run's own id, so the
    engine's artifact paths, the generated decision-gate contract and the settlement
    validator's binding all name one run rather than three.

    ``harness_factory`` exists so a test can substitute the process boundary without
    substituting the adapter, the harness, the graph or the launcher -- everything this
    finding is about stays real.
    """
    runtime = _import_orca_runtime()
    if not objective:
        raise LauncherError(
            f"{ORCA_ADAPTER_REQUIRES_OBJECTIVE}: --adapter orca creates an Orca Run and "
            "needs --objective")
    root = Path(project_root) if project_root is not None else Path.cwd()
    phases = tuple(spec.get("phases") or CANONICAL_PHASES)
    risk = spec.get("risk", "high")
    # `task_context.RISK_SELECTION_SOURCES` is a closed pair, and which member applies is
    # a fact about the launch specification: a run that names its own risk selected it
    # explicitly, a run that does not took the default.
    risk_source = "explicit" if "risk" in spec else "default"
    routing = orca_run_routing(
        agent_profile_name=agent_profile_name,
        requested_phases=tuple(phase.lower() for phase in phases),
        risk=risk, project_root=root)
    factory = harness_factory or runtime.OrcaRuntimeHarness
    try:
        # `artifact_dir` is the BASE the run root is provisioned under -- run_logging
        # appends `artifacts/runs/<run_id>/` itself -- so it is passed before the Run
        # exists and is never rebound afterwards. Rebinding it after `start_run` would
        # move the run's decision ledger out from under the very first pre-dispatch B1
        # guard, which then reads an absence and refuses.
        harness = factory(artifact_base, risk=risk, risk_source=risk_source,
                          agent_routing=routing, quality_profile_root=root)
        harness.preflight()
        run_id = harness.start_run(
            objective, requested_phases=tuple(phase.lower() for phase in phases))
    except runtime.OrcaRuntimeError as exc:
        raise LauncherError(f"{ORCA_RUNTIME_UNAVAILABLE}: {exc}") from exc
    state = build_state({**spec, "run_id": run_id})
    from .orca_adapter import OrcaAdapter
    return OrcaAdapter(harness, runtime_state=runtime_state), state


def demo_results() -> list[dict[str, Any]]:
    """The scripted settlements of a passing canonical 5-phase workflow."""
    results: list[dict[str, Any]] = []
    for phase in CANONICAL_PHASES:
        results.append({"status": "COMPLETE",
                        "unit_test_status": "PASS" if phase == "IMPLEMENTATION" else "NOT_APPLICABLE"})
        results.append({"result": "PASS", "review_verdict": "PASS", "findings": []})
    results.append({"result": "PASS", "review_verdict": "PASS", "findings": []})
    return results


def _read_json(path: str, label: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LauncherError(f"cannot read {label}: {exc}") from exc


def summarize(state: dict[str, Any]) -> dict[str, Any]:
    status = state.get("terminal_status")
    # OS-31: a paused run has no terminal status by design, so the run lifecycle is what
    # names the outcome and selects the exit code.
    lifecycle = state.get("run_lifecycle")
    exit_key = status if status is not None else (
        "WAITING_FOR_INPUT" if lifecycle == "WAITING_FOR_INPUT" else None)
    return {
        "run_id": state.get("run_id"), "workflow_id": state.get("workflow_id"),
        "terminal_status": status, "terminal_reason": state.get("terminal_reason"),
        "run_lifecycle": lifecycle,
        "requested_phases": state.get("requested_phases"),
        "phase_iterations": state.get("phase_iterations"),
        "final_review_iterations": state.get("final_review_iterations"),
        "trace_length": len(state.get("logical_trace") or []),
        "exit_code": EXIT_CODES.get(exit_key, USAGE_EXIT_CODE),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_workflow.py",
        description="Execute the OS-40 deterministic workflow graph.")
    parser.add_argument("--check-runtime", action="store_true",
                        help="only verify the pinned LangGraph runtime and exit")
    parser.add_argument("--demo", action="store_true",
                        help="run the canonical 5-phase workflow with the fake adapter")
    parser.add_argument("--state", help="JSON file describing the initial state")
    parser.add_argument("--results", help="JSON file with the fake adapter's scripted settlements")
    parser.add_argument("--adapter", choices=ADAPTERS, default=FAKE_ADAPTER,
                        help="adapter to execute with: `fake` runs the workflow with no "
                             "Orca runtime present; `orca` is the production path -- it "
                             "creates a real Orca Run and dispatches real agents through "
                             "OrcaAdapter, which is where the bounded validation-repair "
                             "loop actually runs; `standalone` is the OS-37 headless "
                             "runtime, which owns local agent processes itself and needs "
                             "no Orca process, binary, API or terminal lifecycle")
    parser.add_argument("--standalone-profile", default="",
                        help="JSON file describing the driver profile --adapter standalone "
                             "launches with (binary, supported version range, bin_dirs, "
                             "auth secret REFERENCES, and the declared readiness records "
                             "that are READY's only accepting evidence). May instead be "
                             "given as `standalone_profile` inside --state. There is "
                             "deliberately no default: AC-37-03 requires explicit "
                             "configuration rather than a built-in CLI table")
    parser.add_argument("--approval-authority", choices=APPROVAL_AUTHORITIES,
                        default=NO_APPROVAL_AUTHORITY,
                        help="the human-approval authority --adapter standalone composes "
                             "with. `none` (the default) declares no `human_approval` "
                             "capability, so a decision block terminates the run as "
                             "BLOCKED exactly as before; `artifact` names the real OS-30 "
                             "ArtifactHumanApprovalPort over --artifact-base, which makes "
                             "the graph's PAUSE node reachable and the run answerable by "
                             "the `discover`/`resume` verbs. It is deliberately opt-in: "
                             "declaring the capability on a run no human is watching would "
                             "assert an authority that does not exist")
    parser.add_argument("--objective", default="",
                        help="the Run objective (required by --adapter orca)")
    parser.add_argument("--agent-profile", default="",
                        help="agent profile name that routes each role to a real agent "
                             "command (required by --adapter orca)")
    parser.add_argument("--project-root", default=None,
                        help="the project the run drives, used to resolve the agent "
                             "profile and the quality profile (default: the working "
                             "directory)")
    parser.add_argument("--runtime-state",
                        help="JSON file for the durable idempotency ledger "
                             f"(default: ${RUNTIME_STATE_DIR_ENV} or the system temp dir)")
    parser.add_argument("--recursion-limit", type=int,
                        help="override the computed LangGraph recursion limit")
    parser.add_argument("--json", action="store_true", help="print the machine-readable summary")
    parser.add_argument("--artifact-base", default=".",
                        help="root that holds artifacts/runs/<run_id>/ (default: .)")
    parser.add_argument("--checkpoint-store",
                        help="JSON file for the durable OS-40 checkpoint store "
                             f"(default: ${CHECKPOINT_DIR_ENV} or the run artifact root)")
    return parser


# OS-31 caps the CLI at exactly two new verbs.  There is deliberately no run listing, no
# run administration and no general Orca-independent orchestration CLI here.
PAUSE_VERBS = ("discover", "resume")

# OS-44 (BUGFIX-I3-CRITICAL-1).  The invocable Coordinator turn-end boundary.  It is a
# sibling of the pause verbs rather than a flag on the graph launcher because it is not
# a way of RUNNING the workflow: it is the control point a live, prompt-driven
# Coordinator invokes before it returns a response, and it needs no LangGraph -- the
# state it derives is Orca's Task/Dispatch records and the run's own append-only audit.
TURN_VERBS = ("turn-end", "turn-end-hook", "turn-end-bind",
              "turn-end-liveness")
#: OS-43.  New top-level verbs, registered beside the existing tables; every
#: existing dispatch path is untouched, so a revert is dropping the registration.
WATCHDOG_VERBS = ("watchdog", "recover")
#: Iteration-2 review finding B4.  The explicit, audited profile-migration verb -- the ONE
#: sanctioned way to change a run/thread's create-once profile binding.
MIGRATE_VERBS = ("migrate-standalone-profile", "migrate-standalone-prompt-composition")


def build_migrate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_workflow.py",
        description="Explicit, audited migration of a standalone run's bound profile (B4).")
    sub = parser.add_subparsers(dest="verb", required=True)
    migrate = sub.add_parser("migrate-standalone-profile",
                             help="re-bind a standalone run/thread's profile to a new one, "
                                  "writing a durable audit record")
    migrate.add_argument("--run-id", required=True)
    migrate.add_argument("--thread-id", default="")
    migrate.add_argument("--artifact-base", default=".")
    migrate.add_argument("--standalone-profile", required=True,
                         help="JSON file describing the NEW driver profile to bind")
    migrate.add_argument("--actor-id", required=True,
                         help="who is performing the migration (recorded in the audit log)")
    migrate.add_argument("--reason", required=True,
                         help="why the profile is being migrated (recorded in the audit log)")
    migrate.add_argument("--json", action="store_true")
    # Round-8 item 4: the audited remedy for a legacy run that persisted no composition.
    compose = sub.add_parser(
        "migrate-standalone-prompt-composition",
        help="supply the prompt composition of a legacy (pre-fix) standalone run that "
             "persisted none, upgrading its authority under a durable audit record")
    compose.add_argument("--run-id", required=True)
    compose.add_argument("--artifact-base", default=".")
    compose.add_argument("--state", default="",
                         help="the launch state JSON (phases, risk, role_instructions, "
                              "objective) the run was launched with")
    compose.add_argument("--objective", default="",
                         help="the launch objective; overrides the state file's")
    compose.add_argument("--project-root", default=None,
                         help="the project root the launch was composed under "
                              "(default: the current directory)")
    compose.add_argument("--composer-none", action="store_true",
                         help="declare, attributably, that the launch delivered the "
                              "canonical intent payload (no objective)")
    compose.add_argument("--actor-id", required=True)
    compose.add_argument("--reason", required=True)
    compose.add_argument("--json", action="store_true")
    return parser


def _composition_from_migrate_args(args: argparse.Namespace) -> dict[str, Any]:
    """The composition record a ``migrate-standalone-prompt-composition`` invocation
    supplies, built by the SAME function the launch builds it with
    (:func:`prompt_composition_record`), from the same inputs."""
    if args.composer_none:
        if args.objective or args.state:
            raise LauncherError(
                f"{STANDALONE_MIGRATION_REFUSED}: --composer-none declares the canonical "
                "intent payload; it cannot be combined with --objective / --state")
        return prompt_composition_record(None)
    spec: dict[str, Any] = {}
    if args.state:
        loaded = _read_json(args.state, "--state")
        if not isinstance(loaded, dict):
            raise LauncherError("state specification must be a JSON object")
        spec = loaded
    objective = str(args.objective or spec.get("objective") or "")
    if not objective:
        raise LauncherError(
            f"{STANDALONE_MIGRATION_REFUSED}: a prompt-composition migration must either "
            "name the launch objective (--objective / the --state file's `objective`) or "
            "declare --composer-none; a composition cannot be inferred")
    return prompt_composition_record(
        objective, requested_phases=tuple(spec.get("phases") or CANONICAL_PHASES),
        risk=str(spec.get("risk") or "high"),
        project_root=Path(args.project_root) if args.project_root else Path.cwd(),
        role_instructions=spec.get("role_instructions"))


def run_migrate_cli(argv: list[str]) -> int:
    """The ``migrate-standalone-profile`` verb (B4).  Writes an audit record and re-binds
    the authority create-once; refuses an unattributable or no-op migration by name."""
    args = build_migrate_parser().parse_args(argv)
    base = Path(args.artifact_base)
    if args.verb == "migrate-standalone-prompt-composition":
        try:
            audit = migrate_standalone_prompt_composition(
                base, args.run_id, composition=_composition_from_migrate_args(args),
                actor=args.actor_id, reason=args.reason)
        except LauncherError as exc:
            print(f"run_workflow: {exc}", file=sys.stderr)
            return USAGE_EXIT_CODE
        if args.json:
            print(json.dumps(audit, sort_keys=True, ensure_ascii=False))
        else:
            print(f"run={args.run_id} bound prompt composition "
                  f"{audit['bound_prompt_composition_digest']} "
                  f"(composer={audit['bound_prompt_composer']}) by {audit['actor']}")
        return 0
    try:
        new_profile = _read_json(args.standalone_profile, "--standalone-profile")
        if not isinstance(new_profile, dict):
            raise LauncherError("the standalone profile must be a JSON object")
        audit = migrate_standalone_profile(
            base, args.run_id, thread_id=args.thread_id, new_profile_spec=new_profile,
            actor=args.actor_id, reason=args.reason)
    except LauncherError as exc:
        print(f"run_workflow: {exc}", file=sys.stderr)
        return USAGE_EXIT_CODE
    if args.json:
        print(json.dumps(audit, sort_keys=True, ensure_ascii=False))
    else:
        print(f"run={args.run_id} thread={args.thread_id or '-'} "
              f"migrated {audit['old_profile_digest']} -> {audit['new_profile_digest']} "
              f"by {audit['actor']}")
    return 0


def build_turn_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_workflow.py",
        description="The Coordinator turn-end boundary (OS-44).")
    sub = parser.add_subparsers(dest="verb", required=True)
    turn_end = sub.add_parser(
        "turn-end",
        help="derive the run's authoritative state and refuse an early turn end")
    turn_end.add_argument("--run-id", required=True)
    turn_end.add_argument("--artifact-base", default=".")
    turn_end.add_argument(
        "--declare", default="",
        help="the rest state this turn claims to be ending in (COMPLETED, BLOCKED, "
             "ESCALATED, WAITING_FOR_INPUT, ...); corroborated against run state and "
             "never taken on trust")
    turn_end.add_argument(
        "--next-node", default="",
        help="a next node the caller knows about; ADDED to the runnable work the "
             "boundary derived, never a way to remove any of it. The AUTHORITATIVE next "
             "node comes from the run's durable OS-40 checkpoint when it has one")
    turn_end.add_argument(
        "--checkpoint-store",
        help="JSON file for the durable OS-40 checkpoint store, the authority for run "
             "status and graph next node (default: the run artifact root)")
    turn_end.add_argument("--json", action="store_true")
    # FINAL-R1.  The same boundary, as Claude Code's ``Stop`` hook: the runtime invokes
    # it when the model finishes responding, and a refusal BLOCKS the turn instead of
    # being reported after it.  Registration is the operator's opt-in act -- nothing here
    # writes a settings file.  A session the hook cannot attribute to a Run is NOT
    # ignored: what decides is the project's run state, and only proven absence is
    # silent.  FINAL attempt-3 R1 -- see the --run-id help below for the three cases.
    hook = sub.add_parser(
        "turn-end-hook",
        help="the turn-end boundary as a Claude Code Stop hook: reads the hook payload "
             "on stdin, writes the hook decision as JSON on stdout, always exits 0")
    hook.add_argument(
        "--run-id", default="",
        help="the Run this session's Coordinator drives. Default: "
             f"${turn_boundary_env()}, else the durable session binding published by "
             "`turn-end-bind`. With none of the three the project's run state decides: "
             "PROVEN ABSENT -> the turn is allowed in silence; RUNS PRESENT or the run "
             "authority UNREADABLE -> the turn is BLOCKED, bounded by --block-cap; and "
             "when the block budget cannot be recorded, or the cap is spent, the turn "
             "is RELEASED with a diagnostic naming why")
    hook.add_argument("--artifact-base", default=".")
    hook.add_argument(
        "--declare", default="",
        help="a rest state to corroborate, for a registration that knows one; "
             "corroborated against run state exactly as on `turn-end`")
    hook.add_argument(
        "--block-cap", type=int, default=None,
        help="consecutive blocks this hook may issue in one stop chain before it "
             "releases the turn and records the refusal it let through (default: "
             f"${turn_boundary_env(cap=True)}, else "
             "turn_boundary.STOP_HOOK_BLOCK_CAP_DEFAULT)")
    # BUGFIX-I4-R1-REAL-PATH. The producer for the hook's run binding. The registered
    # Stop hook cannot be told which Run a session drives -- the runtime does not know --
    # so the Coordinator says it once here, keyed by the session id Claude Code exports
    # into this process and sends in the hook payload.
    bind = sub.add_parser(
        "turn-end-bind",
        help="bind this Claude Code session to a Run so the registered Stop hook gates "
             "its turn ends (or --release it)")
    bind.add_argument("--run-id", required=True)
    bind.add_argument("--artifact-base", default=".")
    bind.add_argument(
        "--session-id", default="",
        help=f"the session to bind (default: ${turn_boundary_session_env()}, which "
             "Claude Code exports into every command it runs)")
    bind.add_argument(
        "--release", action="store_true",
        help="record that this session has let the Run go, so its later turn ends are "
             "no longer gated on it")
    bind.add_argument("--json", action="store_true")
    # OS-43.  The liveness lease is a SEPARATE record from the binding, and this flag
    # says whether binding also starts publishing it.  Default on, so the ordinary
    # Coordinator gets AC-1's premise without an extra step; `--no-liveness` is for an
    # operator who publishes it from somewhere else.
    liveness = bind.add_mutually_exclusive_group()
    liveness.add_argument("--liveness", dest="liveness", action="store_true",
                          default=True,
                          help="also publish and refresh this run's OS-43 Coordinator "
                               "liveness lease (default)")
    liveness.add_argument("--no-liveness", dest="liveness", action="store_false",
                          help="bind only; publish no liveness lease")
    # A Coordinator that does not run under the harness still needs a way to publish the
    # lease.  Read-only `--status` reports the four-valued read without writing anything.
    live = sub.add_parser(
        "turn-end-liveness",
        help="publish, refresh or report this run's OS-43 Coordinator liveness lease")
    live.add_argument("--run-id", required=True)
    live.add_argument("--artifact-base", default=".")
    live.add_argument("--session-id", default="")
    live.add_argument("--seconds", type=float, default=0.0,
                      help="how long to keep refreshing before releasing; 0 publishes "
                           "one lease and returns, which is what a cron-style caller "
                           "wants")
    live.add_argument("--status", action="store_true",
                      help="report the four-valued liveness read and write nothing")
    live.add_argument("--release", action="store_true",
                      help="record that this Coordinator let the run go")
    live.add_argument("--json", action="store_true")
    return parser


def turn_boundary_session_env() -> str:
    """The session-id variable name the ``turn-end-bind`` help quotes, from the module
    that reads it, so help and behaviour cannot drift."""
    from . import turn_boundary
    return turn_boundary.SESSION_ID_ENV


def turn_boundary_env(*, cap: bool = False) -> str:
    """The environment variable names the ``turn-end-hook`` help text quotes.

    Read from ``turn_boundary`` rather than respelled, so the help can never drift from
    the variable the hook actually reads.
    """
    from . import turn_boundary
    return turn_boundary.STOP_HOOK_CAP_ENV if cap else turn_boundary.STOP_HOOK_RUN_ENV


def run_turn_cli(argv: list[str]) -> int:
    from . import turn_boundary
    args = build_turn_parser().parse_args(argv)
    if args.verb == "turn-end-hook":
        return turn_boundary.run_stop_hook_cli(args)
    if args.verb == "turn-end-bind":
        code = turn_boundary.run_bind_cli(args)
        if getattr(args, "liveness", False) and not getattr(args, "release", False):
            # Additive: the binding's own result is unchanged either way, and a liveness
            # record that cannot be published never fails the bind.
            turn_boundary.begin_run_liveness(
                args.run_id, session_id=args.session_id,
                artifact_base=args.artifact_base)
        return code
    if args.verb == "turn-end-liveness":
        return run_liveness_cli(args)
    return turn_boundary.run_turn_boundary_cli(args)


def run_liveness_cli(args: argparse.Namespace) -> int:
    """`turn-end-liveness`: publish, refresh, release or report the liveness lease."""
    from . import coordinator_liveness
    base = Path(args.artifact_base)
    if args.status:
        status = coordinator_liveness.liveness_status(args.run_id, artifact_base=base)
        payload = {"run_id": args.run_id, "liveness_status": status}
        print(json.dumps(payload, sort_keys=True) if args.json
              else f"run={args.run_id} liveness={status}")
        return 0
    if args.release:
        coordinator_liveness.end_coordinator_liveness(None, args.run_id,
                                                      artifact_base=base)
        status = coordinator_liveness.liveness_status(args.run_id, artifact_base=base)
        payload = {"run_id": args.run_id, "liveness_status": status,
                   "released": True}
        print(json.dumps(payload, sort_keys=True) if args.json
              else f"run={args.run_id} liveness={status} released=1")
        return 0
    keeper = coordinator_liveness.begin_coordinator_liveness(
        args.run_id, artifact_base=base, session_id=args.session_id)
    if keeper is None:
        print(f"run_workflow: could not publish a liveness lease for {args.run_id}",
              file=sys.stderr)
        return USAGE_EXIT_CODE
    if args.seconds and args.seconds > 0:
        import threading
        threading.Event().wait(float(args.seconds))
        coordinator_liveness.end_coordinator_liveness(keeper, args.run_id,
                                                      artifact_base=base)
    else:
        # A cron-style caller publishes ONE lease and returns.  It is deliberately NOT
        # released here: releasing would make the record read ABSENT the instant the
        # command exits, which is the opposite of what a periodic publisher wants.  The
        # lease simply expires on schedule unless the next invocation refreshes it, and
        # the beat thread is retired so nothing outlives the process.
        keeper.stop()
    status = coordinator_liveness.liveness_status(args.run_id, artifact_base=base)
    payload = {"run_id": args.run_id, "liveness_status": status}
    print(json.dumps(payload, sort_keys=True) if args.json
          else f"run={args.run_id} liveness={status}")
    return 0


def build_pause_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_workflow.py", description="Durable pause discovery and resume (OS-31).")
    sub = parser.add_subparsers(dest="verb", required=True)
    discover = sub.add_parser("discover", help="list every paused run under an artifact base")
    discover.add_argument("--artifact-base", default=".")
    discover.add_argument("--json", action="store_true")
    resume = sub.add_parser("resume", help="apply a decision and resume, or dispose, one run")
    resume.add_argument("--run-id", required=True)
    resume.add_argument("--artifact-base", default=".")
    resume.add_argument("--head-sha")
    resume.add_argument("--tree-digest")
    resume.add_argument("--dirty", action="store_true")
    resume.add_argument("--artifact-digest")
    disposition = resume.add_mutually_exclusive_group()
    disposition.add_argument("--cancel", action="store_true")
    disposition.add_argument("--abandon", action="store_true")
    resume.add_argument("--actor-id", default="")
    resume.add_argument("--actor-type", default="human", choices=("human", "service"))
    resume.add_argument("--submission-id", default="")
    resume.add_argument("--reason", default="")
    # Default None, not a constant: the store derives a BOUNDED window that covers the
    # incumbent's whole lease (pause_store.observe_timeout_for), because a window shorter
    # than the lease -- which the old fixed 30s default was against a 60s lease -- can
    # never legally reach takeover in this single call and forced an undocumented retry.
    resume.add_argument("--observe-timeout", type=float, default=None,
                        help="seconds to observe a run another Coordinator holds before "
                             "taking over (default: the owner's whole lease plus "
                             "pause_store.DEFAULT_OBSERVE_GRACE_SECONDS); a shorter "
                             "explicit value is honoured and its PAUSE_OBSERVATION_TIMEOUT "
                             "is a retryable outcome that claims nothing")
    resume.add_argument("--results",
                        help="JSON file with the fake adapter's scripted settlements for "
                             "the round the run re-enters")
    # OS-43 (F-E).  This CLI built FakeAdapter unconditionally, so the shipped one-shot
    # recovery could not resume a real Orca run at all.  The selection DEFAULTS to today's
    # behaviour, so no existing invocation changes meaning and the revert is one line.
    resume.add_argument("--adapter", choices=ADAPTERS, default=FAKE_ADAPTER,
                        help="which execution adapter the resumed round re-enters with "
                             f"(default: {FAKE_ADAPTER})")
    resume.add_argument("--run-owner", default="",
                        help="terminal handle that owns the existing Orca Run; required "
                             "by --adapter orca, which adopts a run rather than creating "
                             "one")
    resume.add_argument("--project-root", default="",
                        help="project root the Orca adapter reads its quality profile "
                             "and agent routing from (default: the working directory)")
    resume.add_argument("--standalone-profile", default="",
                        help="JSON driver profile for --adapter standalone; optional. "
                             "Finding 9: consistent with the watchdog verbs, it is NOT a "
                             "silent override -- it must hash to the digest the launch "
                             "bound, or the resume is refused")
    resume.add_argument("--json", action="store_true")
    return parser


def run_pause_cli(argv: list[str]) -> int:
    """The ``discover``/``resume`` verbs.  ``discover`` works with no LangGraph; ``resume``
    refuses with ``LANGGRAPH_DEPENDENCY_MISSING`` before any claim is taken."""
    args = build_pause_parser().parse_args(argv)
    if args.verb == "discover":
        available = True
        try:
            require_runtime()
        except LauncherError:
            available = False
        from . import pause_runtime
        listings = pause_runtime.discover(args.artifact_base, langgraph_available=available)
        if args.json:
            print(json.dumps([dict(item) for item in listings], sort_keys=True,
                             ensure_ascii=False))
        else:
            for item in listings:
                print(f"{item['run_id']} {item['status'] or '-'} {item['verdict']} "
                      f"phase={item['current_phase'] or '-'} "
                      f"request={item['request_id'] or '-'}")
        return 0
    try:
        require_runtime()
    except LauncherError as exc:
        print(f"run_workflow: {exc}", file=sys.stderr)
        return USAGE_EXIT_CODE
    from . import pause_runtime, pause_store
    from .checkpoint_store import FileCheckpointSaver          # noqa: F401 - import proof
    from .fake_adapter import FakeAdapter
    from .runtime_state import FileRuntimeStateStore
    base = Path(args.artifact_base)
    try:
        approval_port = _artifact_approval_port(base)
        record = pause_store.store_for(args.run_id, artifact_base=base).read(args.run_id)
        if record is None:
            raise LauncherError(f"PAUSE_RECORD_MISSING: no paused run {args.run_id}")
        results = _read_json(args.results, "--results") if args.results else []
        journal = pause_store.journal_for(args.run_id, artifact_base=base)
        selected = getattr(args, "adapter", FAKE_ADAPTER)
        if selected != STANDALONE_ADAPTER:
            # The default ledger is opened for the fake and Orca compositions only: a
            # standalone run reopens the ledger its launch RECORDED (below), and opening
            # the default one beside it would create a second, empty authority.
            ledger = FileRuntimeStateStore(default_runtime_state_path(args.run_id,
                                                                     record["thread_id"]))
            # Follow-up review finding 2.  A standalone-launched run is re-entered
            # standalone or not at all; the fake default is refused on it by name.
            refuse_foreign_composition(base, args.run_id, record["thread_id"],
                                       selected=selected)
        if selected == ORCA_ADAPTER:
            adapter = build_orca_adapter_for_run(
                args.run_id, artifact_base=base, runtime_state=ledger,
                run_owner=args.run_owner,
                project_root=Path(args.project_root) if args.project_root else None)
        elif selected == STANDALONE_ADAPTER:
            # Follow-up review finding 2: END-TO-END.  The refusal that stood here is
            # gone, and what replaced it is the composition the Watchdog already
            # recovers with: the ORIGINAL profile, the ORIGINAL ledger (from the launch
            # binding, never the default path), the ORIGINAL approval authority
            # (finding 8) and the run's own journals -- so the paused round re-enters
            # holding exactly the identity fence, ledger and capabilities it paused with,
            # and an in-flight effect is collected through `resume` (finding 1) rather
            # than re-run.  A run that recorded no binding is refused by name.
            binding = load_standalone_authority(base, args.run_id, record["thread_id"])
            if binding is None:
                raise LauncherError(
                    f"{STANDALONE_ADAPTER_REQUIRES_LEDGER}: run {args.run_id!r} recorded no "
                    "standalone launch binding, so the ledger it paused against cannot be "
                    "reopened; --adapter standalone resumes only a run launched standalone")
            ledger = FileRuntimeStateStore(Path(binding["runtime_state_path"]))
            override: Any = None
            if getattr(args, "standalone_profile", ""):
                override = _read_json(args.standalone_profile, "--standalone-profile")
                if not isinstance(override, dict):
                    raise LauncherError("the standalone profile must be a JSON object")
            adapter, _execution_journal, approval_port = standalone_recovery_composition(
                base, args.run_id, thread_id=record["thread_id"], ledger=ledger,
                pause_row_journal=journal, profile_override=override)
        else:
            adapter = FakeAdapter(results, runtime_state=ledger, run_id=args.run_id,
                                  settlement_journal=journal)

        def graph_factory(saver: Any) -> Any:
            from .graph import build_graph
            return build_graph(adapter, checkpointer=saver, runtime_state=ledger,
                               approval_port=approval_port, journal=journal)

        if args.cancel or args.abandon:
            outcome = pause_runtime.dispose_run(
                args.run_id, artifact_base=base,
                kind="CANCEL" if args.cancel else "ABANDON",
                actor_id=args.actor_id, actor_type=args.actor_type,
                submission_id=args.submission_id, reason=args.reason,
                graph_factory=graph_factory, approval_port=approval_port,
                settlement_port=adapter, observe_timeout_seconds=args.observe_timeout)
            summary = {"run_id": args.run_id, "status": outcome.status,
                       "code": outcome.code, "detail": outcome.detail,
                       "ac1_discharged": outcome.ac1_discharged,
                       "residual_terminals": outcome.residual_terminals}
            exit_code = EXIT_CODES.get(outcome.status, USAGE_EXIT_CODE if
                                       outcome.status == "REFUSED" else 0)
        else:
            projection = record["projection"]
            repository = dict(projection["repository_binding"])
            if args.head_sha:
                repository = {"head_sha": args.head_sha,
                              "tree_digest": args.tree_digest or "clean",
                              "dirty": bool(args.dirty)}
            artifact = dict(projection["artifact_binding"])
            if args.artifact_digest:
                artifact = {**artifact, "digest": args.artifact_digest}
            outcome = pause_runtime.resume_run(
                args.run_id, artifact_base=base, approval_port=approval_port,
                graph_factory=graph_factory, current_repository=repository,
                current_artifact=artifact,
                current_policy_digest=projection["policy_digest"],
                observe_timeout_seconds=args.observe_timeout)
            summary = {"run_id": args.run_id, "status": outcome.status,
                       "code": outcome.code, "detail": outcome.detail,
                       "resumed_checkpoint_id": outcome.resumed_checkpoint_id,
                       "revalidation_codes": list(outcome.revalidation_codes),
                       # A resumed run that paused AGAIN is still waiting on a human, and
                       # reporting only "RESUMED" would hide the new question.
                       "next_pause_record_id": (outcome.next_pause_record or {}).get(
                           "pause_record_id", "")}
            exit_code = 0 if outcome.status in ("RESUMED", "ALREADY_APPLIED",
                                                "NO_EFFECT") else 1
    except LauncherError as exc:
        print(f"run_workflow: {exc}", file=sys.stderr)
        return USAGE_EXIT_CODE
    except pause_store.PauseStoreError as exc:
        # Round-7 blocker 3.  The pause record is the DURABLE THREAD EVIDENCE every
        # resume / cancel / abandon binds to; a store that exists and cannot be read is
        # a typed, fail-closed refusal at this boundary -- adapter-neutral in shape, and
        # named for the standalone composition whose thread fence it protects -- never
        # an unhandled traceback and never an absence.
        print(f"run_workflow: {STANDALONE_THREAD_EVIDENCE_UNREADABLE}: the durable pause "
              f"record of run {args.run_id!r} could not be read ({exc}); an unreadable "
              "authority is not an absent one and the verb is refused", file=sys.stderr)
        return USAGE_EXIT_CODE
    if args.json:
        print(json.dumps(summary, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(f"run={summary['run_id']} status={summary['status']} code={summary['code']}")
    return exit_code


def declared_phases_for_run(run_id: str, *, artifact_base: Path) -> tuple[str, ...]:
    """The workflow phases the STALLED run was launched with, read off its checkpoint.

    ``start_run`` receives ``requested_phases`` from the launch specification, but a
    recovery has no launch specification -- it adopts a run someone else started -- and
    ``resume_run`` leaves the field empty when nobody supplies it.  Empty is not a
    harmless default: ``build_quality_gate_context`` refuses the ``final_review`` gate
    without it ("the final gate re-checks the requested workflow, not a single phase"),
    so an adopted run that reaches its final gate could not dispatch a Final Reviewer at
    all.  The run's own committed checkpoint is the durable authority for what it was
    asked to do, and it is the same document the recovery is about to resume, so reading
    it here cannot disagree with what the engine goes on to execute.

    Lower-cased for the same reason ``build_orca_adapter`` lower-cases the launch
    spec's phases: the engine's state names them in upper case and the task-context
    vocabulary is lower case.

    Returns ``()`` when the head cannot be read, which is exactly the behaviour every
    caller had before this function existed: an unreadable checkpoint fails at the
    engine's own read, not here, and this is not the boundary that should decide it.
    """
    from . import recovery_runtime
    try:
        head = recovery_runtime.resolve_head(run_id, artifact_base=artifact_base)
    except Exception:  # noqa: BLE001 - an unreadable head is the engine's refusal, not ours
        return ()
    if head is None:
        return ()
    phases = head.state.get("requested_phases") or ()
    return tuple(str(phase).lower() for phase in phases)


def build_orca_adapter_for_run(run_id: str, *, artifact_base: Path,
                               runtime_state: Any = None, run_owner: str = "",
                               project_root: Path | None = None,
                               harness_factory: Any = None) -> Any:
    """An ``OrcaAdapter`` bound to an EXISTING Run, for recovery rather than for launch.

    ``build_orca_adapter`` CREATES a Run, which is exactly wrong here: a recovery adopts
    the run that is already stalled.  ``OrcaRuntimeHarness.resume_run`` is the documented
    adoption path and it restores the delivery ledger before returning, so the adapter this
    returns is a successor process in the OS-44 sense rather than a fresh one.
    """
    runtime = _import_orca_runtime()
    if not run_owner:
        raise LauncherError(
            f"{ORCA_ADAPTER_REQUIRES_STATE}: --adapter orca adopts an existing Run and "
            "needs --run-owner, the terminal handle that owns it")
    root = Path(project_root) if project_root is not None else Path.cwd()
    factory = harness_factory or runtime.OrcaRuntimeHarness
    try:
        harness = factory(artifact_base, quality_profile_root=root)
        harness.resume_run(run_id, run_owner=run_owner,
                           requested_phases=declared_phases_for_run(
                               run_id, artifact_base=artifact_base))
    except runtime.OrcaRuntimeError as exc:
        raise LauncherError(f"{ORCA_RUNTIME_UNAVAILABLE}: {exc}") from exc
    from .orca_adapter import OrcaAdapter
    return OrcaAdapter(harness, runtime_state=runtime_state)


def build_watchdog_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_workflow.py",
        description="Stalled-run watchdog and one-shot recovery (OS-43).")
    sub = parser.add_subparsers(dest="verb", required=True)
    watchdog = sub.add_parser("watchdog", help="observe and recover stalled runs")
    modes = watchdog.add_subparsers(dest="mode", required=True)
    for name, help_text in (("once", "exactly one sweep, then return"),
                            ("run", "sweep, wait, repeat until shutdown"),
                            ("status", "fold the ledgers and report; takes no claim")):
        mode = modes.add_parser(name, help=help_text)
        mode.add_argument("--artifact-base", default=".")
        mode.add_argument("--run-id", default="",
                          help="restrict the sweep to one run (default: every run "
                               "discovery reaches)")
        mode.add_argument("--json", action="store_true")
        if name != "status":
            mode.add_argument("--results",
                              help="JSON file with the fake adapter's scripted "
                                   "settlements for the rounds a recovery re-enters")
            mode.add_argument("--max-concurrent-runs", type=int, default=None,
                              help="bound on the per-sweep pool; the default contains "
                                   "sweep amplification against the Orca CLI")
            _add_adapter_selection(mode)
        if name == "run":
            mode.add_argument("--interval-seconds", type=float, default=None,
                              help="sweep cadence (default: the store's own "
                                   "lease-derived observation window)")
            mode.add_argument("--max-sweeps", type=int, default=None)
    recover = sub.add_parser(
        "recover",
        help="one-shot recovery of ONE stalled run through the engine's own API; shares "
             "no state with the watchdog and works with it stopped")
    recover.add_argument("--run-id", required=True)
    recover.add_argument("--artifact-base", default=".")
    recover.add_argument("--results",
                         help="JSON file with the fake adapter's scripted settlements")
    recover.add_argument("--actor-id", default="")
    recover.add_argument("--recursion-limit", type=int, default=None)
    _add_adapter_selection(recover)
    recover.add_argument("--json", action="store_true")
    return parser


def _add_adapter_selection(mode: argparse.ArgumentParser) -> None:
    """CON-5's two compositions, selectable on every verb that can ACT.

    One Supervisor core, two compositions: the standalone/fake one a runtime with no Orca
    can still drive, and the real Orca one an automatically detected stalled Orca run
    needs.  Neither is hardwired -- a watchdog that could only ever build ``FakeAdapter``
    cannot recover a real run at all, and one that could only ever build ``OrcaAdapter``
    would not be runtime-neutral.  The DEFAULT is the fake composition, so no existing
    invocation changes meaning.
    """
    mode.add_argument("--adapter", choices=ADAPTERS, default=FAKE_ADAPTER,
                      help="which execution adapter a recovery re-enters with "
                           f"(default: {FAKE_ADAPTER})")
    mode.add_argument("--run-owner", default="",
                      help="terminal handle that owns the existing Orca Run; required by "
                           "--adapter orca, which adopts a run rather than creating one")
    mode.add_argument("--project-root", default="",
                      help="project root the Orca adapter reads its quality profile and "
                           "agent routing from (default: the working directory)")
    mode.add_argument("--standalone-profile", default="",
                      help="JSON driver profile for --adapter standalone; optional, because "
                           "a standalone run persists its own profile under its run root "
                           "at launch and a recovery reads that by default (finding 5). "
                           "When given, it must hash to the digest the launch bound "
                           "(finding 9): an exact match is a restatement, and any other "
                           "profile is refused rather than silently overriding the "
                           "recovery")


def standalone_approval_port_for(base: Path, run_id: str, thread_id: str = "") -> Any:
    """The EXACT launch-time approval authority of a standalone run, or a refusal.

    Follow-up review finding 8.  The recovery used to hand every standalone run the
    artifact approval port, so a run launched with ``--approval-authority none`` (no
    `human_approval`, decision blocks BLOCK) was recovered declaring `human_approval`
    and could route into a PAUSE its launch never admitted.  The binding is read from the
    same record that names the ledger and rebuilt BY NAME through
    `configured_approval_port`; a record that names none, or one that names an authority
    this composition cannot rebuild, is refused rather than defaulted.
    """
    record = load_standalone_authority(base, run_id, thread_id)
    if record is None:
        # No binding recorded (a launch over an in-memory ledger records nothing).  The
        # fail-closed direction is to declare NOTHING that cannot be proven: `none` --
        # the launcher's own default -- declares no `human_approval`, so the recovered
        # run can BLOCK on a decision but can never PAUSE through an authority its
        # launch is not known to have had.  It is never the artifact port by default.
        return None
    name = record.get("approval_authority")
    if name not in APPROVAL_AUTHORITIES:
        raise LauncherError(
            f"{STANDALONE_APPROVAL_AUTHORITY_MISMATCH}: run {run_id!r} was launched with "
            f"approval authority {name!r}, which this composition cannot rebuild by name; "
            "recovery is refused rather than composed with a different authority")
    return configured_approval_port(str(name), base)


def standalone_recovery_composition(base: Path, run_id: str, *, thread_id: str,
                                    ledger: Any, pause_row_journal: Any,
                                    profile_override: Any = None) -> tuple[Any, Any, Any]:
    """The standalone runtime a RE-ENTRY is composed with: ``(adapter, execution
    journal, approval port)``.  One function for the Watchdog and the `resume` verb.

    Follow-up review finding 2.  The runtime is rebuilt from the CONTENT-ADDRESSED
    profile the launch bound -- `profiles/<digest>.json`, the profile THIS thread
    launched, verified against the digest the run/thread authority recorded -- its ledger
    (the caller resolved it from the same record), and its approval authority restored by
    name (finding 8), so the round a paused or stalled run re-enters is bound to the
    identity fence, the ledger, the profile and the capabilities it held, not to a fresh
    default composition and never to another thread's profile.

    ``profile_override`` (findings 2 / 9) is the ``--standalone-profile`` a recovery may
    carry.  It is NOT a silent override: it must hash to the SAME digest the launch bound,
    in which case it is a restatement of the recorded profile; any other profile is
    refused, because an unverified substitution is exactly the redirection the immutable
    binding exists to prevent.  A run that recorded no authority binding (an in-memory
    ledger) falls back to `profile.json`, and an override there is accepted because there
    is no recorded digest to contradict.

    Nothing here spawns: the adapter adopts in-flight effects through `resume` (finding 1)
    and starts new ones only where the graph dispatches them.
    """
    from .standalone_adapter import StandaloneAdapter
    execution_journal = _standalone_journal_for(base, run_id)
    record = load_standalone_authority(base, run_id, thread_id)
    recorded_digest = str((record or {}).get("profile_digest") or "")
    if profile_override is not None:
        # Finding 9: an override is admitted ONLY as an exact restatement of the bound
        # digest.  A recorded run whose digest the override does not match is refused;
        # a run with no recorded binding (in-memory ledger) has no digest to violate.
        # Iteration 5, B1: the restatement passes the SAME freeze the launch did, against
        # THIS process's cwd -- an operator's relative path means "relative to where I
        # run this" on recovery exactly as it did on launch.  Restated from the launch
        # cwd it freezes to the bound bytes and matches; restated from another cwd it
        # freezes to another worktree and is the typed digest mismatch below, so an
        # override can never re-bind a recovery to a directory the launch did not name.
        profile_override = freeze_profile_worktree(profile_override)
        override_digest = profile_digest(profile_override)
        if recorded_digest and override_digest != recorded_digest:
            raise LauncherError(
                f"{STANDALONE_PROFILE_DIGEST_MISMATCH}: run {run_id!r} (thread "
                f"{thread_id!r}) was launched with profile digest {recorded_digest!r} and "
                f"the --standalone-profile given hashes to {override_digest!r}; a recovery "
                "may restate the recorded profile exactly or migrate it by an explicit "
                "audited act, never override it unverified")
        profile_spec: Any = profile_override
    elif recorded_digest:
        profile_spec = load_standalone_profile(base, run_id, digest=recorded_digest)
    else:
        profile_spec = load_standalone_profile(base, run_id)
    if profile_spec is None:
        raise LauncherError(
            f"{STANDALONE_ADAPTER_REQUIRES_PROFILE}: run {run_id!r} persisted no "
            "driver profile and none was given with --standalone-profile; a "
            "recovery cannot rebuild the runtime it would execute with")
    runtime = build_standalone_runtime(base, run_id, runtime_state=ledger,
                                       profile_spec=profile_spec,
                                       journal=execution_journal)
    approval_port = standalone_approval_port_for(base, run_id, thread_id)
    # ---- round-7 consolidated review, blocker 2 ----------------------------------------
    # The re-entry used to rebuild the adapter WITHOUT the production prompt composer, so
    # the next Worker / Reviewer dispatch after a resume or a watchdog recovery received
    # the raw `ActionIntent` JSON instead of the objective, role, task / review-output
    # contract and correction instruction the launch delivered.  The composition inputs
    # are persisted at launch (`publish_standalone_launch_bindings`) and their digest is
    # bound into the same authority record that names the profile; here they are read
    # back through that binding -- verified by content -- and the SAME renderer is
    # rebuilt by `composer_from_composition`.  A bound composition that is missing,
    # unreadable or does not hash to its digest is the typed refusal
    # `STANDALONE_PROMPT_COMPOSITION_MISSING`: never a silent fallback to intent JSON.
    # A `composer: none` record is the launch's OWN declaration (it named no objective)
    # and is honoured as such.  A run with NO authority binding at all (in-memory ledger,
    # or composed but never launched) reads its current `prompt_composition.json`, and
    # only a run that persisted nothing whatsoever composes nothing.
    recorded_composition = str((record or {}).get("prompt_composition_digest") or "")
    if recorded_composition:
        composition = load_standalone_prompt_composition(base, run_id,
                                                         digest=recorded_composition)
    else:
        composition = load_standalone_prompt_composition(base, run_id)
    adapter = StandaloneAdapter(
        runtime, runtime_state=ledger, settlement_journal=execution_journal,
        pause_row_journal=pause_row_journal, approval_port=approval_port,
        artifact_base=base, run_id=run_id,
        prompt_composer=composer_from_composition(composition))
    return adapter, execution_journal, approval_port


def refuse_foreign_composition(base: Path, run_id: str, thread_id: str, *,
                               selected: str) -> None:
    """Refuse re-entering a STANDALONE-launched run with any other adapter (finding 2).

    A run that recorded a standalone launch binding holds an identity fence, a ledger and
    an approval binding that only the standalone composition can honour.  Selecting --
    or defaulting to -- the fake or Orca adapter on it is refused by name here, before
    any effect, instead of silently composing a runtime that discards all three.

    Finding 3.  An authority record that EXISTS and cannot be read or is not a complete
    standalone binding is a REFUSAL, not an absence: `load_standalone_authority` raises,
    and this used to swallow that and fall through to the foreign composition, so a
    malformed authority file let a fake/Orca re-entry discard the original ledger, fence
    and approval binding.  The raise now propagates -- existing-but-unreadable refuses
    every recovery, exactly as an existing-and-standalone one does.
    """
    record = load_standalone_authority(base, run_id, thread_id)
    if record is not None and record.get("adapter", STANDALONE_ADAPTER) == STANDALONE_ADAPTER:
        raise LauncherError(
            f"{STANDALONE_RUN_ADAPTER_MISMATCH}: run {run_id!r} was launched with "
            f"--adapter {STANDALONE_ADAPTER} and re-entering it needs the same; "
            f"--adapter {selected} would compose a runtime that discards the run's "
            "identity fence, ledger and approval binding")


def _prevalidate_targeted_standalone_authority(base: Path, adapter_name: str,
                                               run_id: str) -> None:
    """B1'/B2'.  For a standalone recovery targeting a SPECIFIC run (`recover`, `watchdog
    once --run-id`), validate the authority at the boundary so a tampered / malformed /
    wrong-thread / incomplete record refuses with the typed authority error BEFORE any
    recovery machinery -- not as a downstream ``RECOVERY_*`` code.  A no-op for any other
    adapter and for a full sweep (no ``--run-id``); a run that recorded no authority loads
    ``None`` and is untouched.  In its own function so the wiring's adapter-neutral
    preamble carries no standalone-only symbol and the fake/Orca arms stay byte-unchanged."""
    if adapter_name == STANDALONE_ADAPTER and run_id:
        load_standalone_authority(base, run_id)


def _watchdog_wiring(args: argparse.Namespace, *, runner: Any = None,
                     harness_factory: Any = None) -> dict[str, Any]:
    """Build the five injected ports for one CLI invocation.

    Every concrete implementation is named HERE, at the wiring boundary, and never inside
    the supervisor core -- which is the whole of CON-5 and is why the same core serves a
    standalone runtime.  ``--adapter`` chooses WHICH composition is built; ``runner`` and
    ``harness_factory`` are the two process boundaries (the ``orca`` CLI and the runtime
    harness) an integration test replaces to drive this same wiring offline.
    """
    from . import recovery_runtime, watchdog_audit
    from .fake_adapter import FakeAdapter
    from .runtime_state import FileRuntimeStateStore, SystemLeaseClock
    from . import pause_store
    base = Path(args.artifact_base)
    results = _read_json(args.results, "--results") if getattr(args, "results", "") else []
    approval_port = _artifact_approval_port(base)
    adapter_name = getattr(args, "adapter", FAKE_ADAPTER)
    if adapter_name == ORCA_ADAPTER:
        # Refused HERE, before discovery lists a single run: an incomplete Orca
        # composition must not be discovered halfway through a sweep, one run at a time.
        if results:
            raise LauncherError(
                f"{ORCA_ADAPTER_REQUIRES_STATE}: --results is the fake adapter's scripted "
                "input; --adapter orca scripts nothing")
        if not getattr(args, "run_owner", ""):
            raise LauncherError(
                f"{ORCA_ADAPTER_REQUIRES_STATE}: --adapter orca adopts an existing Run "
                "and needs --run-owner, the terminal handle that owns it")

    # Iteration-3 finding B1' / B2'.  When a SPECIFIC run is targeted (the `recover` verb
    # always names one; `watchdog once --run-id` does too), the standalone authority is
    # pre-validated at the wiring boundary, BEFORE any recovery machinery runs, so a
    # tampered / malformed / wrong-thread / incomplete authority is refused with the typed
    # authority error and never masked by a downstream `RECOVERY_*` code from a lazy load
    # inside the recovery invocation.  Extracted into a helper (not inlined here) so this
    # adapter-neutral preamble stays free of any standalone-only symbol -- the fake and
    # Orca arms are byte-unchanged, which `test_os37_lifecycle_boundary_regressions`
    # asserts by scanning this region.  A full sweep (no `--run-id`) is deliberately NOT
    # pre-validated: one corrupt run must not abort the whole fleet.
    _prevalidate_targeted_standalone_authority(base, adapter_name, getattr(args, "run_id", ""))

    adapters: dict[str, Any] = {}
    bindings: dict[str, Any] = {}
    threads: dict[str, str] = {}
    approvals: dict[str, Any] = {}

    def bindings_for(run_id: str) -> Any:
        """This run's durable ledger and journal.  Adopts nothing and claims nothing.

        The ledger is keyed on the run's THREAD id, and the thread id is read from the
        run's own durable record: the pause record when the run is paused, and the
        committed checkpoint head when it is a stalled ACTIVE run with no pause record.
        The head used to be consulted for neither, and a stalled active run -- the one
        case the Watchdog exists for -- was therefore bound to a ledger named after the
        run id rather than its thread, i.e. an EMPTY ledger in which every claim reads
        `CREATED`; a recovery driven through this wiring re-entered the graph holding none
        of the run's receipts and settlements.  Surfaced by the consolidated follow-up
        review's finding 5 verification (watchdog discovery -> recovery -> a subsequent
        execution node, asserted on the run's OWN ledger); it is adapter-neutral and the
        Orca and fake arms below are untouched.
        """
        if run_id not in bindings:
            record = pause_store.store_for(run_id, artifact_base=base).read(run_id)
            thread_id = (record or {}).get("thread_id") or ""
            if not thread_id:
                try:
                    head = recovery_runtime.resolve_head(run_id, artifact_base=base)
                except Exception:  # noqa: BLE001 - an unreadable head is refused later, by name
                    head = None
                thread_id = getattr(head, "thread_id", "") or ""
            # The thread the run's OWN durable record names; "" when nothing names one,
            # in which case the launch binding read below is the run's PRIMARY one.
            known_thread = thread_id
            thread_id = thread_id or run_id
            ledger_path = default_runtime_state_path(run_id, thread_id)
            if adapter_name == STANDALONE_ADAPTER:
                # Round 4, finding 2.  A standalone run launched with `--runtime-state`
                # was recovered against the DEFAULT ledger -- a different, empty authority
                # in which every claim reads CREATED -- so an already-settled intent was
                # re-executed and the run then failed SETTLEMENT_IDENTITY_MISMATCH against
                # the real one.  The launch records its ledger beside the profile, and the
                # recovery reopens EXACTLY that.  A run that recorded none keeps the
                # default, which is what its launch used.  The Orca and fake arms are
                # untouched.
                authority = load_standalone_authority(base, run_id, known_thread)
                if authority is not None:
                    ledger_path = Path(authority["runtime_state_path"])
            bindings[run_id] = (
                FileRuntimeStateStore(ledger_path),
                pause_store.journal_for(run_id, artifact_base=base))
            threads[run_id] = known_thread
        return bindings[run_id]

    def approval_for(run_id: str) -> Any:
        """The launch-time approval authority of a STANDALONE run, restored EXACTLY
        (follow-up review finding 8), or a refusal by name.  Read from the same record
        that names the ledger; a run that recorded no binding, or one this wiring cannot
        rebuild by name, is refused rather than handed the artifact port by default."""
        if run_id not in approvals:
            bindings_for(run_id)
            approvals[run_id] = standalone_approval_port_for(
                base, run_id, threads.get(run_id, ""))
        return approvals[run_id]

    def adapter_for(run_id: str) -> Any:
        if run_id not in adapters:
            ledger, journal = bindings_for(run_id)
            if adapter_name != STANDALONE_ADAPTER:
                # Follow-up review finding 2 / 8.  A run that RECORDED a standalone launch
                # binding is re-entered with the standalone composition or not at all:
                # composing the fake (or Orca) adapter over it would discard the identity
                # fence, the ledger and the approval binding the run holds.
                refuse_foreign_composition(base, run_id, threads.get(run_id, ""),
                                           selected=adapter_name)
            if adapter_name == ORCA_ADAPTER:
                # The REAL runtime, adopting the run that is already stalled.  Built per
                # run, because the harness a recovery adopts is the stalled run's own.
                adapter: Any = build_orca_adapter_for_run(
                    run_id, artifact_base=base, runtime_state=ledger,
                    run_owner=args.run_owner,
                    project_root=(Path(args.project_root)
                                  if getattr(args, "project_root", "") else None),
                    harness_factory=harness_factory)
            elif adapter_name == STANDALONE_ADAPTER:
                # OS-37 W-3.  The standalone runtime a recovery re-enters with is bound to
                # THIS run's own durable journal and ledger, exactly as the Orca branch
                # binds the stalled run's own harness.
                #
                # Consolidated review finding 5.  It is bound to a REAL runtime, rebuilt
                # from the profile the run persisted at launch (or one the operator names
                # with --standalone-profile).  This adapter is the one the recovered graph
                # EXECUTES with: `runtime=None` was honest for the capability question
                # (`capabilities_for` below still asks it that way, touching no process)
                # but the recovered graph's EXECUTE_INTENT then called `start()` on it and
                # died in `_require_runtime`, so a stalled standalone run became
                # `escalation_observation_undecidable` forever instead of being recovered.
                spec_path = getattr(args, "standalone_profile", "")
                override: Any = None
                if spec_path:
                    override = _read_json(spec_path, "--standalone-profile")
                    if not isinstance(override, dict):
                        raise LauncherError("the standalone profile must be a JSON object")
                adapter, _execution_journal, _port = standalone_recovery_composition(
                    base, run_id, thread_id=threads.get(run_id, ""), ledger=ledger,
                    pause_row_journal=journal, profile_override=override)
            else:
                adapter = FakeAdapter(list(results), runtime_state=ledger,
                                      run_id=run_id, settlement_journal=journal,
                                      approval_port=approval_port)
            adapters[run_id] = (adapter, ledger, journal)
        return adapters[run_id]

    def graph_factory_for(run_id: str) -> Any:
        adapter, ledger, journal = adapter_for(run_id)
        # Finding 8: the recovered graph's PAUSE node publishes through the SAME
        # authority the adapter declares, which for a standalone run is the recorded one.
        port = (approval_for(run_id) if adapter_name == STANDALONE_ADAPTER
                else approval_port)

        def factory(saver: Any) -> Any:
            from .graph import build_graph
            return build_graph(adapter, checkpointer=saver, runtime_state=ledger,
                               approval_port=port, journal=journal)
        return factory

    def capabilities_for(run_id: str) -> Any:
        """F11's capability authority: the adapter that would EXECUTE the recovery.

        Wired here rather than in the core, so a standalone runtime supplies its own and
        the Watchdog never has to guess what a runtime can do.

        The Orca composition answers from an adapter built over this run's ledger and
        journal but bound to NO harness, because ``capabilities()`` is a declaration of
        what the adapter type and its wiring support and reads no harness at all.  That
        matters: adopting the Run is what ``OrcaRuntimeHarness.resume_run`` does, and it
        publishes a Coordinator liveness lease -- so adopting a run merely to ASK what it
        can do would make the run look alive to the very gate that is about to decide
        whether it is stalled.  Adoption therefore happens at recovery time, for runs the
        gate has already cleared, and never during observation.
        """
        if adapter_name == ORCA_ADAPTER:
            from .orca_adapter import OrcaAdapter
            ledger, journal = bindings_for(run_id)
            return OrcaAdapter(None, runtime_state=ledger, settlement_journal=journal,
                               approval_port=approval_port).capabilities()
        if adapter_name == STANDALONE_ADAPTER:
            # OS-37 D10.2.  `runtime=None` mirrors the Orca branch above for the same
            # stated reason: asking what an adapter can do must not spawn, adopt or touch
            # anything, or the run would look alive to the very gate deciding whether it
            # is stalled.  This branch reads no process at all.
            from .standalone_adapter import StandaloneAdapter
            from .watchdog_observation import ObservationUnavailable
            # Round-9 item 3: PER-RUN ISOLATION.  `bindings_for` / `approval_for` read
            # THIS run's launch authority, and a malformed / unreadable / wrong-thread
            # record there is a typed `LauncherError` -- which, raised through the
            # observation port, aborted the WHOLE fleet sweep (one corrupt run left
            # every healthy stalled run unobserved).  It is converted here, at the
            # standalone composition boundary, into the observation port's own typed
            # refusal for this run: the sweep records the run as unreadable at
            # observation, its recovery composition (`graph_factory_for`, the same read)
            # is refused by name with the authority error in the row's detail, and the
            # other runs are classified and recovered.  A TARGETED `--run-id` invocation
            # is pre-validated at the wiring boundary above and still fails closed.
            try:
                ledger, _journal = bindings_for(run_id)
                port = approval_for(run_id)
            except LauncherError as exc:
                raise ObservationUnavailable(f"{run_id}: {exc}") from exc
            return StandaloneAdapter(
                None, runtime_state=ledger,
                settlement_journal=_standalone_journal_for(base, run_id),
                # Finding 8: the capability the run DECLARED at launch, restored from
                # its record -- never the wiring's default port.
                approval_port=port, artifact_base=base,
                run_id=run_id).capabilities()
        return adapter_for(run_id)[0].capabilities()

    from . import turn_boundary
    wiring = WatchdogWiring({
        "discovery": recovery_runtime.RunDiscovery(base),
        # The Orca listing authority is the real CLI boundary.  Where no `orca` binary
        # answers, the read RAISES and the sweep fails closed at R1 -- it never reads
        # "no dispatch is running" out of silence.
        # OS-37 DD-3 / W-3.  A standalone deployment has no `orca` binary to list
        # dispatches, so the Orca listing authority would raise for every run and the
        # sweep would fail closed at R1 forever.  The standalone observation answers the
        # same fact from the run's OWN durable journal, keeping the three-way discipline
        # exactly: unreadable RAISES (F1), "no open dispatch" is an empty tuple -- an
        # ABSENCE, not an UNSUPPORTED -- and an uncovered fact still raises
        # ObservationUnsupported.  F6/F7 are NOT relaxed; they gain a real authority.
        # The orca and fake arms are byte-unchanged.
        "observation": (
            _standalone_observation(
                base, capabilities_for,
                # Finding 1: the liveness probe reads the run's OWN launch-recorded
                # ledger (bindings_for honours the recorded runtime-state path), so the
                # receipt fence and exit sentinel decide F6, not the open journal row.
                ledger_factory=lambda run_id: bindings_for(run_id)[0])
            if adapter_name == STANDALONE_ADAPTER
            else recovery_runtime.RunObservationAdapter(
                base, runner=runner or turn_boundary._default_runner,
                capabilities=capabilities_for)),
        "liveness": recovery_runtime.CoordinatorLivenessReader(base),
        # The gate and the outcome->action table BOTH read this clock: `react` stamps a
        # backoff deadline on it and a later sweep -- in a later process -- decides
        # against it whether that deadline has lapsed.  Without one, `watchdog_state`
        # reads `0.0` for "now" on both sides, so every deadline it wrote stayed in the
        # future forever and one REFUSED or CONFLICT outcome blocked its identity
        # permanently.  SC-7 is a BOUNDED retry with backoff, not a stop.
        "clock": SystemLeaseClock(),
        # The factory is handed over PER RUN and resolved at request time, so every run
        # discovery reaches -- not merely a single `--run-id` -- gets the graph for its
        # own thread, ledger, journal and adapter.  The placeholder that returned `None`
        # for the default all-runs mode is gone: it could only ever raise inside
        # `graph.invoke`, and a sweep would have reported that as a run it had acted on.
        "recovery": recovery_runtime.EngineRecoveryInvocation(
            artifact_base=base, approval_port=approval_port,
            graph_factory_for=graph_factory_for,
            recursion_limit=getattr(args, "recursion_limit", None)),
        "audit": watchdog_audit.FileWatchdogAudit(base),
    })
    # Round 4, finding 2: the per-run composition itself, as an ATTRIBUTE rather than a
    # key -- the dict is unpacked straight into `watchdog_supervisor.sweep(**deps)`, whose
    # signature is closed -- so a test can ask which ledger a recovery would reopen
    # without driving a whole recovery.
    wiring.adapter_for = adapter_for
    return wiring


class WatchdogWiring(dict):
    """The five injected ports (a plain mapping for ``sweep(**deps)``) plus, as an
    attribute the supervisor never sees, the per-run adapter composition."""

    adapter_for: Any


def run_watchdog_cli(argv: list[str], *, runner: Any = None,
                     harness_factory: Any = None) -> int:
    """The ``watchdog`` and ``recover`` verbs."""
    from . import ports, recovery_runtime, watchdog_audit, watchdog_supervisor
    args = build_watchdog_parser().parse_args(argv)
    base = Path(args.artifact_base)
    if args.verb == "recover":
        # AC-9: this path calls the engine API DIRECTLY and imports no watchdog module.
        try:
            wiring = _watchdog_wiring(args, runner=runner,
                                      harness_factory=harness_factory)
            request = wiring["recovery"].build_request(
                run_id=args.run_id,
                recovery_kind=recovery_runtime.RECOVERY_KIND_STALLED_ACTIVE)
        except LauncherError as exc:
            print(f"run_workflow: {exc}", file=sys.stderr)
            return USAGE_EXIT_CODE
        except ports.RecoveryPreconditionUnavailable as exc:
            # Named, and reported as a REFUSAL to start rather than as an outcome: no
            # claim was taken and no effect was attempted.
            summary = {"run_id": args.run_id, "status": "", "code": exc.code,
                       "recovery_id": "", "recovery_kind": "",
                       "effect_performed": False, "head_before": "", "head_after": "",
                       "detail": exc.detail}
            print(json.dumps(summary, sort_keys=True, ensure_ascii=False, default=str)
                  if args.json
                  else f"run={args.run_id} status=- code={exc.code}")
            return 1
        outcome = recovery_runtime.recover_stalled_run(request)
        summary = {"run_id": args.run_id, "status": outcome.status,
                   "code": outcome.code, "recovery_id": outcome.recovery_id,
                   "recovery_kind": outcome.recovery_kind,
                   "effect_performed": outcome.effect_performed,
                   "head_before": outcome.head_before, "head_after": outcome.head_after,
                   "detail": outcome.detail}
        print(json.dumps(summary, sort_keys=True, ensure_ascii=False, default=str)
              if args.json
              else f"run={args.run_id} status={outcome.status} code={outcome.code}")
        return 0 if outcome.status in (recovery_runtime.RECOVERED,
                                       recovery_runtime.NO_EFFECT) else 1
    if args.mode == "status":
        audit = watchdog_audit.FileWatchdogAudit(base)
        rows = []
        for listing in recovery_runtime.RunDiscovery(base).discover():
            run_id = str(listing["run_id"])
            if args.run_id and run_id != args.run_id:
                continue
            try:
                folded = audit.fold(run_id)
            except watchdog_audit.WatchdogAuditError as exc:
                rows.append({"run_id": run_id, "ledger": "UNREADABLE",
                             "detail": str(exc)})
                continue
            rows.append({"run_id": run_id, "verdict": listing["verdict"],
                         "identities": {key: dict(value)
                                        for key, value in folded.items()}})
        print(json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str)
              if args.json
              else "\n".join(f"{row['run_id']} {row.get('verdict', '')} "
                             f"identities={len(row.get('identities', {}))}"
                             for row in rows))
        return 0
    try:
        wiring = _watchdog_wiring(args, runner=runner, harness_factory=harness_factory)
    except LauncherError as exc:
        print(f"run_workflow: {exc}", file=sys.stderr)
        return USAGE_EXIT_CODE
    deps: dict[str, Any] = dict(wiring)
    if args.max_concurrent_runs:
        deps["max_concurrent_runs"] = int(args.max_concurrent_runs)
    if args.run_id:
        deps["run_ids"] = (args.run_id,)
    if args.mode == "once":
        report = watchdog_supervisor.run_once(**deps)
    else:
        report = watchdog_supervisor.run_continuous(
            interval_seconds=args.interval_seconds, max_sweeps=args.max_sweeps, **deps)
    summary = {"runs_observed": report.runs_observed, "runs_acted": report.runs_acted,
               "escalations": list(report.escalations),
               "runs": [vars(row) for row in report.runs]}
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False, default=str)
          if args.json
          else (f"observed={report.runs_observed} acted={report.runs_acted} "
                f"escalations={len(report.escalations)}"))
    return report.exit_code


def _artifact_approval_port(base: Path) -> Any:
    try:
        from scripts.clarification_protocol import ArtifactHumanApprovalPort
    except ImportError:  # installed Skill layout exposes sibling tools directly
        from clarification_protocol import ArtifactHumanApprovalPort  # type: ignore
    return ArtifactHumanApprovalPort(base)


def configured_approval_port(authority: str, base: Path) -> Any:
    """The approval authority the OPERATOR named, or ``None``.  R4's whole conditionality.

    ``None`` is not a degraded port and is never substituted for one: it is the absence of
    an authority, and the adapter's own ``capabilities()`` reads it as such and withdraws
    ``human_approval``.  That is the same discipline the recovery capabilities already
    follow -- declared on the wiring that makes them honourable, withdrawn when it is
    absent -- rather than a flag that turns a declaration on while nothing backs it.

    Refused by name for an unknown member so a typo cannot silently compose a run with no
    authority under a flag that says it has one; ``build_parser`` already constrains the
    command line, and this is the second line of defence for a programmatic caller.
    """
    if authority == NO_APPROVAL_AUTHORITY:
        return None
    if authority == ARTIFACT_APPROVAL_AUTHORITY:
        return _artifact_approval_port(base)
    raise LauncherError(
        f"{UNKNOWN_APPROVAL_AUTHORITY}: {authority!r} is not one of "
        f"{', '.join(APPROVAL_AUTHORITIES)}")


def run_cli(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in PAUSE_VERBS:
        return run_pause_cli(raw)
    if raw and raw[0] in TURN_VERBS:
        return run_turn_cli(raw)
    if raw and raw[0] in WATCHDOG_VERBS:
        return run_watchdog_cli(raw)
    if raw and raw[0] in MIGRATE_VERBS:
        return run_migrate_cli(raw)
    args = build_parser().parse_args(argv)
    try:
        version = require_runtime()
        if args.check_runtime:
            print(f"deterministic workflow runtime ready (langgraph {version})")
            return 0
        state, results, orca_spec = _launch_inputs(args)
        from .runtime_state import FileRuntimeStateStore
        runtime_state: Any = None
        if args.adapter == STANDALONE_ADAPTER:
            # OS-37 WI-13.  The standalone composition, in the SAME order the Orca path
            # composes in -- with the LEDGER moved ahead of the adapter, which is external
            # review finding #10.  The ledger has to exist first because the state this
            # call returns carries a SNAPSHOT of `adapter.capabilities()`, and
            # `external_resume` is withdrawn while the identity fence has no ledger to live
            # in; taking the snapshot first made every standalone run declare no recovery
            # capability at all.  Unlike the Orca path this needs no Run to exist -- there
            # is no external Run -- so the run id comes from the launch spec and names only
            # this runtime's own files.
            resolved_run = orca_spec.get("run_id", "") or state.get("run_id", "")
            runtime_state = FileRuntimeStateStore(
                Path(args.runtime_state) if args.runtime_state
                else default_runtime_state_path(resolved_run, state["thread_id"]))
            # R4.  The approval authority is resolved HERE, at the composition root, and
            # threaded in with the ledger -- before the capability snapshot the state
            # carries.  `--adapter orca` and `--adapter fake` are deliberately NOT given
            # this: what an Orca run declares is `OrcaAdapter`'s own answer and changing it
            # is authorized by no acceptance criterion here.
            # Finding 5.  When the launch names an OBJECTIVE (the task contract), the
            # composition root builds the production prompt renderer from it, so
            # EXECUTE_INTENT -> StandaloneAdapter.start delivers the role / phase / task
            # contract / correction instruction / review-output contract to the real CLI
            # rather than the canonical intent JSON.  A launch with no objective renders
            # nothing (the pre-finding behaviour) -- the objective is required only for a
            # run whose agents must be told what to do.
            # Round-7 blocker 2: the composition is handed over as the DATA record that
            # `execute_state` persists after the claim (and the authority binds), so a
            # resume / watchdog recovery rebuilds the identical renderer from it.
            objective = str(args.objective or orca_spec.get("objective") or "")
            composition = prompt_composition_record(
                objective or None,
                requested_phases=tuple(orca_spec.get("phases") or CANONICAL_PHASES),
                risk=str(orca_spec.get("risk") or "high"),
                project_root=(Path(args.project_root)
                              if getattr(args, "project_root", None) else Path.cwd()),
                role_instructions=orca_spec.get("role_instructions"))
            adapter, state = build_standalone_adapter(
                orca_spec, artifact_base=Path(args.artifact_base),
                run_id=resolved_run, runtime_state=runtime_state,
                profile_spec=_standalone_profile_spec(args),
                approval_port=configured_approval_port(
                    args.approval_authority, Path(args.artifact_base)),
                prompt_composition=composition)
        if args.adapter == ORCA_ADAPTER:
            # The production path.  The Run is created FIRST, because the run id it
            # returns is what the state, the artifact paths and the ledger are all named
            # after -- deriving any of them from the launch specification would name a
            # run that does not exist.  Nothing durable is written before this point, so
            # a refusal here leaves no Task, no Dispatch, no terminal and no ledger.
            adapter, state = build_orca_adapter(
                orca_spec, objective=args.objective,
                artifact_base=Path(args.artifact_base),
                agent_profile_name=args.agent_profile, project_root=args.project_root)
        # Durable by default: without an explicit path the run still gets a real on-disk
        # ledger, because an unguarded default is exactly what lets a restart duplicate an
        # external Task/Dispatch.  The standalone branch already built one above, and
        # rebuilding it here would point the adapter at a ledger the capability snapshot
        # was not taken against.
        if runtime_state is None:
            ledger_path = (Path(args.runtime_state) if args.runtime_state
                           else default_runtime_state_path(state["run_id"],
                                                           state["thread_id"]))
            runtime_state = FileRuntimeStateStore(ledger_path)
        if args.adapter == ORCA_ADAPTER:
            adapter.runtime_state = runtime_state
        elif args.adapter == STANDALONE_ADAPTER:
            pass          # already threaded into the adapter AND its runtime, before the
                          # capability snapshot that the state carries.
        else:
            from .fake_adapter import FakeAdapter
            adapter = FakeAdapter(results, runtime_state=runtime_state)
        graph_extras: dict[str, Any] = {}
        pause_rows = getattr(adapter, "pause_row_journal", None)
        if pause_rows is not None:
            # OS-37 external review #9, wired HERE rather than in `graph.py`.  `build_graph`
            # otherwise falls back to `adapter.settlement_journal`, which for the standalone
            # adapter is the append-only `ExecutionJournal` -- an object with no `row()` and
            # no `record()`.  The PAUSE and DISPOSE nodes call both, the `AttributeError`
            # was swallowed by `_settlement_row`'s broad handler, and every pause on a
            # standalone run was reported as `DISPATCH_UNACCOUNTED`.  `graph.py` is a pinned
            # policy module and this ticket adds no branch to it; the journal seam it
            # already exposes is the correct place to say which journal this composition
            # means.
            graph_extras["journal"] = pause_rows
        final = execute_state(state, adapter=adapter, runtime_state=runtime_state,
                              recursion_limit=args.recursion_limit,
                              checkpoint_store_path=args.checkpoint_store,
                              artifact_base=Path(args.artifact_base), **graph_extras)
    except LauncherError as exc:
        print(f"run_workflow: {exc}", file=sys.stderr)
        return USAGE_EXIT_CODE
    summary = summarize(final)
    if args.json:
        print(json.dumps(summary, sort_keys=True, ensure_ascii=False))
    else:
        reason = (summary["terminal_reason"] or {}).get("code")
        print(f"terminal_status={summary['terminal_status']} reason={reason} "
              f"phases={summary['requested_phases']} steps={summary['trace_length']}")
    return summary["exit_code"]


def _standalone_profile_spec(args: argparse.Namespace) -> dict[str, Any] | None:
    """The driver profile, from ``--standalone-profile`` or from the state spec.

    Returns ``None`` when neither supplies one, so ``build_standalone_adapter`` refuses by
    name.  There is deliberately no default profile: AC-37-03 requires explicit
    configuration, and a built-in CLI table is exactly what U7 is routed around.
    """
    path = getattr(args, "standalone_profile", "")
    if path:
        spec = _read_json(path, "--standalone-profile")
        if not isinstance(spec, dict):
            raise LauncherError("the standalone profile must be a JSON object")
        return spec
    if getattr(args, "state", ""):
        state_spec = _read_json(args.state, "--state")
        if isinstance(state_spec, dict) and isinstance(
                state_spec.get("standalone_profile"), dict):
            return state_spec["standalone_profile"]
    return None


def _launch_inputs(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """``(state, scripted results, the raw launch spec)``.

    The raw spec is returned as well because the production adapter rebuilds the state
    around the Orca Run id it is about to create, which is not knowable here.
    """
    if args.adapter == ORCA_ADAPTER:
        # A production run scripts nothing: `--results` is the fake adapter's input and
        # naming one here would be a contradiction, not a convenience.
        if not args.state:
            raise LauncherError(
                f"{ORCA_ADAPTER_REQUIRES_STATE}: --adapter orca needs --state")
        spec = _read_json(args.state, "--state")
        if not isinstance(spec, dict):
            raise LauncherError("state specification must be a JSON object")
        return build_state(spec), [], spec
    if args.adapter == STANDALONE_ADAPTER:
        # A standalone run scripts nothing either, for the same reason: it drives real
        # local agent processes.  The state built here is PROVISIONAL -- `main` rebuilds it
        # through `build_standalone_state` around the live adapter's own capabilities, which
        # is D-2(b) -- so this call exists only to validate the spec and refuse early.
        if not args.state:
            raise LauncherError(
                f"{STANDALONE_ADAPTER_REQUIRES_STATE}: --adapter standalone needs --state")
        if args.results:
            raise LauncherError(
                f"{STANDALONE_ADAPTER_REQUIRES_STATE}: --results is the fake adapter's "
                "scripted input; --adapter standalone drives real local processes and "
                "scripts nothing")
        spec = _read_json(args.state, "--state")
        if not isinstance(spec, dict):
            raise LauncherError("state specification must be a JSON object")
        return build_state(spec), [], spec
    if args.demo:
        spec = {"run_id": "run_demo", "thread_id": "demo",
                "phases": list(CANONICAL_PHASES)}
        return build_state(spec), demo_results(), spec
    if not args.state or not args.results:
        raise LauncherError("--demo, or both --state and --results, are required")
    results = _read_json(args.results, "--results")
    if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
        raise LauncherError("--results must be a JSON list of settlement result objects")
    spec = _read_json(args.state, "--state")
    return build_state(spec), results, spec


def require_runtime() -> str:
    """Fail explicitly when the pinned LangGraph runtime is absent; never fall back."""
    import importlib.metadata
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError as exc:
        raise LauncherError("LANGGRAPH_DEPENDENCY_MISSING: install requirements-langgraph.txt") from exc
    try:
        version = importlib.metadata.version("langgraph")
    except importlib.metadata.PackageNotFoundError as exc:
        raise LauncherError("LANGGRAPH_DEPENDENCY_MISSING: distribution metadata absent") from exc
    if version != "0.2.76":
        raise LauncherError(f"LANGGRAPH_VERSION_UNSUPPORTED: {version}")
    return version
