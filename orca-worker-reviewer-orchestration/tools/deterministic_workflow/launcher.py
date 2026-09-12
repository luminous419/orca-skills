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


def _standalone_observation(artifact_base: Any, capabilities: Any) -> Any:
    """The OS-37 standalone ``RunObservationPort``.  Invokes no Orca CLI."""
    from .standalone_adapter import StandaloneRunObservation
    return StandaloneRunObservation(
        artifact_base,
        journal_factory=lambda run_id: _standalone_journal_for(artifact_base, run_id),
        capabilities=capabilities)


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
    target = standalone_profile_path(artifact_base, run_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    def _plain(value: Any) -> Any:
        # A Python caller may hand over `dataclasses.asdict(profile)`, whose
        # `graceful_hint` is bytes; the JSON door re-encodes a str hint with `.encode()`,
        # so the round trip through the persisted file is exact for any UTF-8 hint.
        if isinstance(value, bytes):
            return value.decode("utf-8", "surrogateescape")
        raise TypeError(f"the profile spec is not JSON-shaped: {type(value).__name__}")
    payload = json.dumps(dict(profile_spec), sort_keys=True, indent=2, ensure_ascii=False,
                         default=_plain)
    import hashlib
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    archive = target.parent / "profiles" / f"{digest}.json"
    archive.parent.mkdir(parents=True, exist_ok=True)

    def _write(path: Path) -> None:
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    if not archive.exists():
        _write(archive)
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


def load_standalone_profile(artifact_base: Any, run_id: str) -> dict[str, Any] | None:
    """The persisted profile spec, or ``None`` when the run never persisted one."""
    target = standalone_profile_path(artifact_base, run_id)
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


def build_standalone_adapter(spec: dict[str, Any], *, artifact_base: Path,
                             run_id: str = "", runtime_state: Any = None,
                             profile_spec: Any = None,
                             approval_port: Any = None) -> tuple[Any, dict[str, Any]]:
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
    # `or None` keeps the old behaviour for a profile that declares no worktree: the
    # session then falls back to `os.getcwd()` as before, and nothing about a run that
    # never named one changes.
    runtime = build_standalone_runtime(artifact_base, resolved_run,
                                       runtime_state=runtime_state,
                                       profile_spec=profile_spec, journal=journal)
    # Finding 5.  The profile is persisted under the run root BEFORE any effect, so the
    # Watchdog can rebuild this exact runtime for a recovery instead of binding the
    # recovered graph to an adapter with no runtime at all.
    persist_standalone_profile(artifact_base, resolved_run, profile_spec)
    adapter = StandaloneAdapter(runtime, runtime_state=runtime_state,
                                settlement_journal=journal,
                                pause_row_journal=_standalone_pause_row_journal(
                                    artifact_base, resolved_run),
                                approval_port=approval_port,
                                artifact_base=artifact_base, run_id=resolved_run)
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
    return _finalize_pause_if_waiting(final, checkpointer=checkpointer,
                                      artifact_base=artifact_base)


#: The named refusal a pause that cannot be RECORDED reports.  It is a member of
#: `pause_policy.PAUSE_REFUSAL_CODES`, so `terminal_node` already prints it as the reason a
#: run BLOCKED rather than folding it into an ordinary decision block.
PAUSE_RECORD_NOT_WRITTEN = "PAUSE_RECORD_MISSING"


def _finalize_pause_if_waiting(final: dict[str, Any], *, checkpointer: Any,
                               artifact_base: Path | None) -> dict[str, Any]:
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

    Deliberately OUTSIDE the held execution-authority section: the record is the pause's
    own durable authority and is claimed through `pause_store`, not through the run's
    execution lease, and writing it under a lease that `_settle_or_release` has already let
    go would be claiming an ownership this caller no longer has.

    **Fail-closed.**  A pause that cannot be recorded must not be REPORTED as a pause:
    nothing could ever resume it, and exit code 4 would tell an operator to wait for a
    human on a run no `discover` will ever list.  The refusal is converted into the
    ordinary BLOCKED terminal, named, exactly as `pause_node`'s own refusals are.

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
        return _pause_not_recorded(final, str(exc), code=exc.code)
    except (OSError, ValueError, KeyError) as exc:  # noqa: BLE001 - unrecorded is refused
        return _pause_not_recorded(final, f"{type(exc).__name__}: {exc}")
    return final


def _pause_not_recorded(final: dict[str, Any], detail: str,
                        code: str = PAUSE_RECORD_NOT_WRITTEN) -> dict[str, Any]:
    """Turn an unrecordable pause into the BLOCKED terminal, with the reason named."""
    blocked = dict(final)
    blocked["run_lifecycle"] = "ACTIVE"
    blocked["route_token"] = "BLOCK"
    blocked["terminal_reason"] = {"code": code, "message": detail}
    return terminal_node(blocked)


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
        ledger = FileRuntimeStateStore(default_runtime_state_path(args.run_id,
                                                                 record["thread_id"]))
        journal = pause_store.journal_for(args.run_id, artifact_base=base)
        selected = getattr(args, "adapter", FAKE_ADAPTER)
        if selected == ORCA_ADAPTER:
            adapter = build_orca_adapter_for_run(
                args.run_id, artifact_base=base, runtime_state=ledger,
                run_owner=args.run_owner,
                project_root=Path(args.project_root) if args.project_root else None)
        elif selected == STANDALONE_ADAPTER:
            # An EXPLICIT REFUSAL, not a fall-through to the fake composition.  `resume`
            # re-enters a run whose paused round was dispatched to a process this
            # invocation does not own: the standalone runtime's authority is its own
            # journal and ledger, and re-entering a round with a fresh, process-less
            # adapter would silently discard the identity fence the paused round holds.
            # Named here so an operator gets a refusal rather than a wrong composition.
            raise LauncherError(
                f"{STANDALONE_ADAPTER_UNSUPPORTED_HERE}: --adapter standalone cannot "
                "resume a paused round from this CLI; the standalone runtime's dispatch "
                "state is re-queried through standalone_journal.rediscover in the process "
                "that owns the session")
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
                           "at launch and a recovery reads that by default (finding 5)")


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

    adapters: dict[str, Any] = {}
    bindings: dict[str, Any] = {}

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
                thread_id = getattr(head, "thread_id", "") or run_id
            bindings[run_id] = (
                FileRuntimeStateStore(default_runtime_state_path(run_id, thread_id)),
                pause_store.journal_for(run_id, artifact_base=base))
        return bindings[run_id]

    def adapter_for(run_id: str) -> Any:
        if run_id not in adapters:
            ledger, journal = bindings_for(run_id)
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
                from .standalone_adapter import StandaloneAdapter
                execution_journal = _standalone_journal_for(base, run_id)
                spec_path = getattr(args, "standalone_profile", "")
                if spec_path:
                    profile_spec: Any = _read_json(spec_path, "--standalone-profile")
                    if not isinstance(profile_spec, dict):
                        raise LauncherError("the standalone profile must be a JSON object")
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
                adapter = StandaloneAdapter(
                    runtime, runtime_state=ledger,
                    settlement_journal=execution_journal,
                    pause_row_journal=journal,
                    approval_port=approval_port, artifact_base=base, run_id=run_id)
            else:
                adapter = FakeAdapter(list(results), runtime_state=ledger,
                                      run_id=run_id, settlement_journal=journal,
                                      approval_port=approval_port)
            adapters[run_id] = (adapter, ledger, journal)
        return adapters[run_id]

    def graph_factory_for(run_id: str) -> Any:
        adapter, ledger, journal = adapter_for(run_id)

        def factory(saver: Any) -> Any:
            from .graph import build_graph
            return build_graph(adapter, checkpointer=saver, runtime_state=ledger,
                               approval_port=approval_port, journal=journal)
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
            ledger, _journal = bindings_for(run_id)
            return StandaloneAdapter(
                None, runtime_state=ledger,
                settlement_journal=_standalone_journal_for(base, run_id),
                approval_port=approval_port, artifact_base=base,
                run_id=run_id).capabilities()
        return adapter_for(run_id)[0].capabilities()

    from . import turn_boundary
    return {
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
            _standalone_observation(base, capabilities_for)
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
    }


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
            adapter, state = build_standalone_adapter(
                orca_spec, artifact_base=Path(args.artifact_base),
                run_id=resolved_run, runtime_state=runtime_state,
                profile_spec=_standalone_profile_spec(args),
                approval_port=configured_approval_port(
                    args.approval_authority, Path(args.artifact_base)))
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
