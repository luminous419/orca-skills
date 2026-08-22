#!/usr/bin/env python3
"""Small real-Orca integration harness using deterministic fake agents."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.task_context import (
        CANONICAL_PHASES,
        FINAL_REVIEW_PHASE,
        build_reviewer_context,
        build_task_boundary,
        phase_artifact_contract,
        render_task_spec,
        require_workflow_phase,
        strip_task_context,
    )
except ModuleNotFoundError:  # direct `python3 scripts/...` execution
    from task_context import (
        CANONICAL_PHASES,
        FINAL_REVIEW_PHASE,
        build_reviewer_context,
        build_task_boundary,
        phase_artifact_contract,
        render_task_spec,
        require_workflow_phase,
        strip_task_context,
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_CODEX = REPO_ROOT / "scripts" / "fake_bin" / "codex"
WAIT_TYPES = "worker_done,escalation,question"
SUPPORTED_ORCA_APP_VERSION = "1.4.184"
REQUIRED_ORCHESTRATION_GUIDE_SNIPPETS = (
    "orca orchestration run-create --objective <text> --json",
    # The bare "--spec <text>" form is a prefix of the entry below; keeping both would
    # make the dependency-grammar regression test vacuous (DESIGN R-12).
    "orca orchestration task-create --spec <text> [--deps <json_array>]",
    "orca orchestration task-list [--status <status>] [--ready]",
    "orca orchestration dispatch --task <task_id> --to <handle>",
    "orca orchestration dispatch-show --task <task_id>",
    "orca orchestration worker-start --task <task_id>",
    "worker-start --task <next_task_id> --terminal <handle> --json",
    "orca orchestration worker-show --dispatch <dispatch_id> --json",
    "orca orchestration check --wait --types worker_done,escalation,question",
    "orca orchestration worker-release --dispatch <dispatch_id> --json",
    "orca orchestration worker-retain --dispatch <dispatch_id> --json",
    "--type worker_done --subject \"<status>\"",
    "--task-id <task_id> --dispatch-id <dispatch_id> --outcome succeeded",
)
REQUIRED_ORCA_CLI_GUIDE_SNIPPETS = (
    "orca terminal create",
    "orca terminal send",
    "ORCA terminal wait",
    "terminal wait --terminal <handle> --for tui-idle",
)

# ---- lifecycle role vocabulary -------------------------------------------------
# Same tokens as the SKILL.md anchor block, widened by one fixture-only role. The
# harness may widen the never-close set; it must never narrow it (DESIGN C-9).
SKILL_TERMINAL_ROLE_CLASSES = frozenset(
    {
        "coordinator_session",
        "setup_terminal",
        "active_worker",
        "external_or_adopted",
        "phase_worker",
        "phase_reviewer",
        "unknown_role",
    }
)
HARNESS_ONLY_ROLES = frozenset({"run_owner_fixture"})
TERMINAL_ROLE_CLASSES = SKILL_TERMINAL_ROLE_CLASSES | HARNESS_ONLY_ROLES
NEVER_CLOSE_ROLES = frozenset(
    {
        "coordinator_session",
        "run_owner_fixture",
        "setup_terminal",
        "active_worker",
        "external_or_adopted",
        "unknown_role",
    }
)
CLOSE_ELIGIBLE_ROLES = frozenset({"phase_worker", "phase_reviewer"})
TERMINAL_ORIGINS = frozenset({"self_created", "adopted", "pre_existing", "unknown"})
CLEANUP_AUTHORITY_STATES = frozenset({"authorized", "not_authorized", "unknown"})
SELF_HANDLE_ENV = "ORCA_TERMINAL_HANDLE"

# ---- lifecycle mutation vocabulary ---------------------------------------------
WORKER_RESOURCE_OUTCOMES = ("reuse", "retain", "release", "unsupervised")
# ---- W-15: reuse leaves this map on purpose ------------------------------------
# reuse issues NO lifecycle mutation: ownership transfers when the next Task is
# started on the same terminal (SKILL.md section 6, "#### 1. Immediate worker
# reuse"). Deliberately absent from this map so settle_attempt has nothing to send.
LIFECYCLE_TO_COMMAND = {
    "retain": "worker-retain",
    "release": "worker-release",
}
LIFECYCLE_MUTATION_COMMANDS = frozenset(
    {"worker-release", "worker-retain", "worker-abandon", "close"}
)
# The coordinator's three lifecycle *choices* (SKILL.md section 6, outcomes 1-3).
# The fourth outcome, "unsupervised", is an observation about the Dispatch and can
# never be chosen, so it is deliberately absent here.
#
# No longer derived from the map above: reuse is still one of the coordinator's three
# lifecycle choices, it simply has no command. Deriving it would make account_axes
# raise on every reuse (see its `lifecycle not in LIFECYCLE_INTENTS` gate).
LIFECYCLE_INTENTS = frozenset({"reuse", "retain", "release"})
# reuse and retain both mean "this terminal is handed onward alive". Neither may ever
# be accounted as a close or a release, on the supervised branch or the unsupervised
# one, no matter what cleanup authority the terminal has.
RETAIN_INTENTS = frozenset({"reuse", "retain"})

# ---- W-29 (D-6 / R8-iii) -------------------------------------------------------
# A release receipt whose process action is one of these proves the runtime really
# ended the process. Anything else -- "none" above all, which is what all 24 observed
# release receipts carry -- means the terminal is still alive and must be recorded as
# retained, whatever cleanup authority said. (PLAN D-6 / R8-iii, ANALYSIS F-3 result 2.)
PROCESS_TERMINATING_ACTIONS = frozenset({"killed", "terminated", "exited"})
SETTLEMENT_STATES = ("absent", "in_progress", "finalized")
# Worker states that mean "this dispatch produced no outcome and the process is gone".
# `outcome_unknown` is the agent that started and died; `ready` is the agent that had
# already exited when worker-start adopted its terminal -- reachable because ladder
# rung 3 observes TUI idle before adopting. Both take the same abandon recovery, and
# neither may be read as a settlement.
UNSETTLED_WORKER_STATES = frozenset({"outcome_unknown", "ready"})

# ---- reuse gate allowlists (all three are POSITIVE lists, never denylists) ------
# The ownership value the 25 observed rung-3 receipts carry. A terminal the runtime
# does not own is exactly the terminal whose ownership the next worker start can
# take. Fail-closed on purpose: any other value -- including "" and whatever a rung-1
# (runtime-created) terminal would report, which this repo has never observed (A-7)
# -- is NOT transferable.
OWNERSHIP_TRANSFERABLE_STATES = frozenset({"external"})
# `account_axes` already refuses to call a terminal "live" when the receipt is
# missing (a missing terminalResource is `disputed`, see that method); the reuse gate
# must be at least as strict, so an empty or unrecognized value is NOT live and never
# becomes reusable. `not_requested` is the value all 25 observed rung-3 receipts
# carry for a live, retained, external terminal; `active` is the value the offline
# fixtures carry. An unobserved value must not be guessed into this set (A-7): if the
# runtime ever reports another live value the fix is to add it here WITH a recorded
# receipt, and until then reuse falls back to a fresh terminal -- which is exactly
# today's behaviour, so failing closed costs correctness nothing.
LIVE_RELEASE_STATES = frozenset({"not_requested", "active"})
# Worker states that prove the previous dispatch reached an outcome and its agent
# process is no longer mid-flight. `succeeded` is the observed real receipt;
# `settled` is the offline fixture value; `failed` is included because
# SETTLED_OUTCOMES and SETTLED_STATUSES both already name `failed` as a real settled
# outcome -- a failed-but-settled dispatch is still a settled one. `failed` is
# DERIVED, not observed. Deliberately excluded: "" (missing), `running` (still
# mid-flight), every member of UNSETTLED_WORKER_STATES, and `abandoned`.
REUSABLE_WORKER_STATES = frozenset({"succeeded", "settled", "failed"})
# Task/Dispatch provenance values that prove a Dispatch really reached an outcome.
# Anything else -- `dispatched` above all -- is not-settled, and axis (a) forbids a
# lifecycle mutation on it. This is the vocabulary of SKILL.md section 6 axis (a),
# not of the worker registry: it is read from the Dispatch row and the Task row.
SETTLED_STATUSES = frozenset({"completed", "failed"})
# The only two outcomes an accepted `worker_done` may carry. Same vocabulary as the
# dispatch preamble's `--outcome succeeded|failed` (REQUIRED_ORCHESTRATION_GUIDE_
# SNIPPETS above) and as the fake-worker contract. A payload without one of these is
# not an accepted settlement message at all, so axis (a) refuses it before STEP 2.
SETTLED_OUTCOMES = frozenset({"succeeded", "failed"})
# Identity fields every accepted `worker_done` payload carries, mapped to the value
# they must equal. SKILL.md section 6 axis (a) requires the message to match the
# EXPECTED Task and Dispatch ID; neither is optional, because a payload that names
# no dispatch proves nothing about the dispatch we are about to mutate.
WORKER_DONE_IDENTITY_FIELDS = ("dispatchId", "taskId")
# Where a Dispatch row records the completion timestamp axis (a) requires as
# provenance. The live runtime writes `completed_at` (snake_case, on both the
# `completed` and the `failed` row); the camelCase spellings are accepted so a
# JSON-cased projection of the same row is not read as "never completed".
COMPLETION_TIMESTAMP_KEYS = ("completed_at", "completedAt", "settled_at", "settledAt")


def completion_timestamp(dispatch_row: dict[str, Any]) -> str | None:
    """The Dispatch row's completion timestamp, or None when it carries none.

    Read-only and total: an unknown row shape answers None rather than raising, so
    the caller decides what a missing timestamp means.
    """
    for key in COMPLETION_TIMESTAMP_KEYS:
        value = dispatch_row.get(key)
        if value:
            return str(value)
    return None


class OrcaRuntimeError(RuntimeError):
    pass


class UnsupportedOrcaContract(OrcaRuntimeError):
    pass


def validate_orca_contract(
    app_version: str, orchestration_guide: str, cli_guide: str
) -> None:
    if app_version != SUPPORTED_ORCA_APP_VERSION:
        raise UnsupportedOrcaContract(
            f"runtime harness supports Orca {SUPPORTED_ORCA_APP_VERSION}; "
            f"installed runtime is {app_version}"
        )
    missing = [
        snippet
        for snippet in REQUIRED_ORCHESTRATION_GUIDE_SNIPPETS
        if snippet not in orchestration_guide
    ]
    missing.extend(
        snippet
        for snippet in REQUIRED_ORCA_CLI_GUIDE_SNIPPETS
        if snippet not in cli_guide
    )
    if missing:
        raise UnsupportedOrcaContract(
            "installed version-matched guide does not match the pinned grammar: "
            + ", ".join(missing)
        )



def cleanup_authority(role: str, origin: str, owned_by_this_dispatch: bool) -> str:
    """Axis (c2). Role gate first (STEP 4-0), provenance second (STEP 4a/4b).

    There is deliberately no branch that returns "authorized" from origin alone:
    a self-created terminal may still be the coordinator's own session.
    """
    if role == "unknown_role" or role not in TERMINAL_ROLE_CLASSES:
        return "unknown"
    if role in NEVER_CLOSE_ROLES:
        return "not_authorized"
    if role not in CLOSE_ELIGIBLE_ROLES:
        return "unknown"
    if origin != "self_created" or not owned_by_this_dispatch:
        return "unknown"
    return "authorized"


def close_allowed(role: str, origin: str, owned_by_this_dispatch: bool) -> bool:
    """Code-level mirror of CLOSE_ALLOWED_ONLY_WHEN = authorized_and_close_eligible_role.

    Requires the close-eligible role a second time, so loosening cleanup_authority()
    alone still cannot open the close path (defense in depth).
    """
    return (
        role in CLOSE_ELIGIBLE_ROLES
        and cleanup_authority(role, origin, owned_by_this_dispatch) == "authorized"
    )


def _flag_value(args: list[str], flag: str) -> str | None:
    for index, token in enumerate(args):
        if token == flag and index + 1 < len(args):
            return args[index + 1]
    return None


@dataclass(frozen=True)
class ReuseObservation:
    """One fresh, read-only pre-reuse look at a terminal, taken for ONE dispatch.

    Every field is copied straight out of a `worker-show` result; nothing is derived
    and nothing is remembered from an earlier attempt. `observed_at_dispatch` is what
    makes a stale record detectable: reuse_eligible() refuses a record that was not
    taken for the dispatch it is being asked about.

    Every field defaults to "" because PROBE_ARGUMENTS completeness requires it. ""
    means NOT OBSERVED -- never "fine". No judgement anywhere in reuse_eligible()
    reads "" as safe: conditions 3 and 5 are positive allowlist membership tests, so
    "" fails them automatically. Same direction as account_axes treating a missing
    terminalResource as `disputed`.
    """

    observed_at_dispatch: str = ""   # the dispatch this look was taken for
    handle: str = ""                 # terminal handle the look is about
    worker_state: str = ""           # worker.state
    release_state: str = ""          # terminalResource.releaseState
    ownership_state: str = ""        # terminalResource.ownershipState
    retained_reason: str = ""        # terminalResource.retainedReason


@dataclass
class RuntimeAttempt:
    role: str
    iteration: int
    task_id: str
    dispatch_id: str
    outcome: str
    task_status: str
    dispatch_status: str
    worker_state: str
    terminal_state: str
    lifecycle_action: str
    worker_done_count: int
    execution_path: str
    body: str = ""
    settlement: str = ""  # axis (a)
    worker_resource: str = ""  # axis (b): reuse|retain|release|unsupervised
    process_liveness: str = ""  # axis (c1): live|already exited|disputed
    cleanup_authority: str = ""  # axis (c2): authorized|not_authorized|unknown
    terminal_role: str = "unknown_role"
    finalizations: int = 0
    # ---- reuse instrumentation (W-17): every field defaults, so the existing
    # positional constructions in this module and in the tests stay valid.
    terminal: str = ""
    terminal_created: bool = False
    terminal_effect: str = ""  # worker-start receipt: created|reused|""
    release_process_action: str = ""  # release/retain receipt: none|killed|...
    task_boundary: tuple[tuple[str, str], ...] = ()  # layer-1 payload, frozen
    reviewer_context_keys: tuple[str, ...] = ()  # 8 keys when role is reviewer


@dataclass
class RuntimeScenarioResult:
    scenario: str
    run_id: str
    status: str
    iteration: int
    attempts: list[RuntimeAttempt] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    recovery: list[str] = field(default_factory=list)
    run_owner_handle: str = ""
    ledger: list[dict[str, Any]] = field(default_factory=list)
    fixture_teardown: dict[str, Any] = field(default_factory=dict)
    reviewer_task_id: str = ""
    reviewer_task_status: str = ""
    late_dependent_status: str = ""
    commands_used: list[str] = field(default_factory=list)
    final_review_terminals: list[str] = field(default_factory=list)
    phase_reviewer_terminals: list[str] = field(default_factory=list)
    # ---- reuse aggregates (W-18), filled by finish() before the ledger is cleared
    reuse_chains: dict[str, list[str]] = field(default_factory=dict)
    terminal_creations: int = 0
    retained_terminals: list[str] = field(default_factory=list)


def worker_start_terminal_effect(worker_start_result: dict[str, Any]) -> str:
    """The `action` of the terminal effect in a worker-start result, or "".

    Total on purpose. 25 of 25 observed receipts carry
    {"kind": "terminal", "action": "reused", ...}, but a receipt without that effect
    must read as "not recorded", never as a guess. A module-level function, not a
    method, so it is not swept by the public-method probe in the contract tests.
    """
    for effect in worker_start_result.get("effects") or ():
        if isinstance(effect, dict) and effect.get("kind") == "terminal":
            return str(effect.get("action") or "")
    return ""


@dataclass(frozen=True)
class WorkflowEvidence:
    """What the workflow can actually show at the moment of one dispatch.

    PR #12 MAJOR-1: the Reviewer keys are how a REUSED session learns what the new
    task is, so filling them with values derived from the fake agent's behaviour
    script tells the reviewer nothing true. Every field here is something the caller
    already holds when it dispatches -- the artifacts earlier phases had approved, the
    artifact this phase produced, what the worker actually claimed, and the outcome
    the runtime actually settled -- so the context is a reference to real workflow
    state instead of a placeholder shaped like one.

    Empty is the honest answer for a dispatch with nothing behind it yet (the first
    phase has no approved baseline), which is why the defaults are empty rather than
    invented.
    """

    original_objective: str = ""
    approved_baseline: tuple[str, ...] = ()
    current_delta: tuple[str, ...] = ()
    new_claims: tuple[str, ...] = ()
    validation: tuple[str, ...] = ()


def dispatch_context(
    role: str,
    iteration: int,
    mode: str,
    *,
    phase: str | None = None,
    base_spec: str | None = None,
    findings: tuple[str, ...] = (),
    resolutions: dict[str, str] | None = None,
    evidence: WorkflowEvidence | None = None,
    run_id: str = "",
) -> tuple[str, dict[str, str], dict[str, Any] | None]:
    """The Task spec text an agent will actually receive, plus what went into it.

    Returns (spec, boundary, reviewer_context). Every caller runs this BEFORE
    `task-create` and BEFORE `worker-start`, which is the whole correction behind
    FINAL-I1-MAJOR-1: the layer-1 boundary and the Reviewer's eight keys have to be
    part of the dispatched input, not metadata assembled once the attempt is over.
    Both agent-visible channels carry the same string -- the Task spec, which Orca
    replays into the dispatch preamble, and the low-level `terminal send` prompt.

    `run_id` is the current Orca Run's id and is threaded straight into every
    phase_artifact_contract() call below -- it is what keeps this run's
    artifact_contract, current_delta and approved_baseline references inside
    artifacts/runs/<run_id>/ instead of the shared artifacts/ root every other run
    also writes to. It defaults to "" (never used as-is) so phase is still checked,
    and reported, before run_id is.

    `mode` and `phase` are two different axes and this function keeps them apart.
    `mode` is the fake agent's behaviour script ("complete" / "pass" / "fail" /
    "exit"); `phase` is the workflow stage ("analysis".."test"), and it is the ONLY
    thing that may become current_phase. PR #12 MAJOR-1 was current_phase=mode: keys
    that looked right carrying a value that was not a phase at all. `phase` is
    keyword-only and fail-closed -- require_workflow_phase raises for a missing or
    unknown value rather than reaching for the mode that is conveniently in scope,
    because that silent fallback IS the defect. It carries a `None` default only so
    the public-method probe can still bind every parameter; passing None raises.

    Nothing here can put an id in the payload: build_task_boundary has no such
    parameter, and both ids are unknown at this point anyway. That is what makes
    TASK_BOUNDARY_NEVER_CARRIED structural rather than a habit.

    A module-level function, not a method, so it is not swept by the public-method
    probe in the contract tests (same reason as worker_start_terminal_effect).
    """
    phase = require_workflow_phase(phase, field="phase")
    is_reviewer = role.endswith("reviewer")
    current_role = "reviewer" if is_reviewer else "worker"
    # Trimmed, not used raw: run_attempt renders once for task-create and hands the
    # result back in, so an untrimmed base would quote a whole rendered block into
    # the Reviewer's original_objective on the second pass.
    base = strip_task_context(
        base_spec if base_spec is not None else f"{role} iteration {iteration}: {phase}"
    )
    artifact_contract = phase_artifact_contract(
        role=current_role, phase=phase, run_id=run_id
    )
    boundary = build_task_boundary(
        current_role=current_role,
        current_phase=phase,
        current_iteration=iteration,
        artifact_contract=artifact_contract,
        relevant_previous_findings=findings,
    )
    reviewer_context: dict[str, Any] | None = None
    if is_reviewer:
        # Every value is derivable before the dispatch exists. The previous wiring
        # fed this builder the attempt's own body and outcome, which is precisely why
        # it could only ever run after settlement -- a Reviewer cannot be handed its
        # own future answer as context.
        evidence = evidence or WorkflowEvidence()
        # The delta a reviewer reads is the WORKER's deliverable for this phase, not
        # the reviewer's own artifact contract: same phase, worker side of the pair.
        worker_artifact = phase_artifact_contract(
            role="worker", phase=phase, run_id=run_id
        )
        reviewer_context = build_reviewer_context(
            original_objective=evidence.original_objective or base,
            current_phase=phase,
            approved_baseline=evidence.approved_baseline,
            current_delta=evidence.current_delta or (worker_artifact,),
            new_claims=evidence.new_claims,
            previous_findings=tuple(
                (finding, (resolutions or {}).get(finding, ""))
                for finding in findings
            ),
            validation=evidence.validation,
            # The real tree this review may verify against, spelled the way
            # E2EHarness spells its own workspace: a path, not a description.
            drill_down=(str(REPO_ROOT),),
        )
    return render_task_spec(base, boundary, reviewer_context), boundary, reviewer_context


class OrcaRuntimeHarness:
    def __init__(self, artifact_dir: Path, *, wait_timeout_ms: int = 10000) -> None:
        self.orca = self._resolve_orca()
        self.artifact_dir = artifact_dir
        self.wait_timeout_ms = wait_timeout_ms
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.run_owner: str | None = None
        self.run_id: str | None = None
        self._raw: list[dict[str, Any]] = []
        self._signals: list[str] = []
        # handle -> terminal row (authoritative role/origin, survives across dispatches)
        self._terminals: dict[str, dict[str, Any]] = {}
        # dispatch_id -> lifecycle row (axis outcomes + finalization state)
        self._ledger: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _resolve_orca() -> str:
        configured = os.environ.get("ORCA_CLI_COMMAND")
        executable = configured or shutil.which("orca")
        if not executable:
            raise OrcaRuntimeError("Orca CLI executable was not found")
        return executable

    def _exec_orca(self, args: tuple[str, ...]) -> tuple[int, str]:
        """The ONLY process boundary in this harness. Returns (returncode, stdout).

        Offline tests replace THIS method -- never call(). Replacing call() would bypass
        self._raw, which lifecycle_commands() is derived from.
        """
        completed = subprocess.run(
            [self.orca, *args, "--json"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        return completed.returncode, completed.stdout

    def call(self, *args: str, allow_error: bool = False) -> dict[str, Any]:
        returncode, stdout = self._exec_orca(tuple(args))
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise OrcaRuntimeError(
                f"non-JSON Orca response for {' '.join(args)}: {stdout!r}"
            ) from exc
        # A failed command is still a command that was sent: record it before raising.
        self._raw.append({"command": list(args), "response": payload})
        if (returncode != 0 or not payload.get("ok")) and not allow_error:
            raise OrcaRuntimeError(
                f"Orca command failed ({' '.join(args)}): {payload.get('error')}"
            )
        return payload

    # ---- lifecycle ledger ------------------------------------------------

    def register_terminal(
        self,
        handle: str,
        *,
        role: str,
        origin: str,
        intended_role: str | None = None,
        owner_dispatch_id: str | None = None,
        created_by: str = "",
        agent_command: str = "",
    ) -> dict[str, Any]:
        """Create the ledger row for a terminal at creation/adoption time.

        role and origin are the only axis (c2) evidence that exists, and the runtime
        keeps neither, so they are recorded here or lost forever.
        """
        if role not in TERMINAL_ROLE_CLASSES:
            raise OrcaRuntimeError(f"unknown terminal role: {role}")
        if origin not in TERMINAL_ORIGINS:
            raise OrcaRuntimeError(f"unknown terminal origin: {origin}")
        row = self._terminals.get(handle)
        if row is None:
            row = self._terminals[handle] = {
                "handle": handle,
                "role": role,
                "origin": origin,
                "intended_role": intended_role or role,
                "owner_dispatch_id": owner_dispatch_id,
                "created_by": created_by,
                "policy_commands": [],
                "tui_idle": "unobserved",
                # ---- reuse gate evidence -----------------------------------
                "agent_command": agent_command,
                # retain_requested has exactly ONE path to True: an explicit user
                # retain. It is not a parameter, so the default False means "no
                # retain was ever requested" rather than "nobody said otherwise".
                "retain_requested": False,
                "retain_reason": "",
                "terminal_effect": "",
                "owner_dispatch_ids": [owner_dispatch_id] if owner_dispatch_id else [],
            }
        else:  # ownership transfer, never a role promotion (reuse chain)
            row["owner_dispatch_id"] = owner_dispatch_id or row["owner_dispatch_id"]
            if created_by:
                row["created_by"] = created_by
            if agent_command:                 # never overwrite a recorded value blank
                row["agent_command"] = agent_command
            if owner_dispatch_id and (
                not row["owner_dispatch_ids"]
                or row["owner_dispatch_ids"][-1] != owner_dispatch_id
            ):
                row["owner_dispatch_ids"].append(owner_dispatch_id)
        row["cleanup_authority"] = cleanup_authority(
            row["role"], row["origin"], row["owner_dispatch_id"] is not None
        )
        row["action"] = "retained"
        return row

    def _attach_terminal(
        self, handle: str, dispatch_id: str, created_by: str
    ) -> dict[str, Any]:
        """Bind a handle to the dispatch that now owns it.

        A handle already in the ledger keeps its recorded role (ownership transfer,
        see the reuse outcome); an unseen handle is an adoption.
        """
        if handle in self._terminals:
            row = self.register_terminal(
                handle,
                role=self._terminals[handle]["role"],
                origin=self._terminals[handle]["origin"],
                owner_dispatch_id=dispatch_id,
                created_by=created_by,
            )
            # A dispatch that has not settled yet owns an `active_worker`, whatever it
            # was called before (SKILL.md STEP 4-0: a close before settle is an axis
            # (a) violation). demote_or_promote_role already supports the round trip:
            # the demotion is conservativeness 0 -> 1 here, and settle_attempt
            # performs the only allowed upward transition once axis (a) has confirmed.
            self.demote_or_promote_role(handle, "active_worker", settled=False)
            return row
        return self.register_terminal(
            handle,
            role="external_or_adopted",
            origin="adopted",
            owner_dispatch_id=dispatch_id,
            created_by=created_by,
        )

    def demote_or_promote_role(
        self, handle: str, new_role: str, *, settled: bool
    ) -> None:
        """The only allowed upward transition is active_worker -> phase_* once settled."""
        row = self._terminals.get(handle)
        if row is None or row["role"] == new_role:
            return
        current = row["role"]
        if new_role in CLOSE_ELIGIBLE_ROLES:
            if current == "active_worker" and settled:
                row["role"] = new_role
            return
        conservativeness = {
            "phase_worker": 0,
            "phase_reviewer": 0,
            "active_worker": 1,
            "external_or_adopted": 2,
            "unknown_role": 3,
        }
        if (
            current in conservativeness
            and new_role in conservativeness
            and conservativeness[new_role] > conservativeness[current]
        ):
            row["role"] = new_role

    def ledger_terminal(self, handle: str) -> dict[str, Any]:
        """Public read accessor; an unregistered handle reads back as unknown_role."""
        row = self._terminals.get(handle)
        if row is not None:
            return row
        return {
            "handle": handle,
            "role": "unknown_role",
            "origin": "unknown",
            "intended_role": "unknown_role",
            "owner_dispatch_id": None,
            "created_by": "",
            "policy_commands": [],
            "tui_idle": "unobserved",
            "cleanup_authority": "unknown",
            "action": "retained",
            "agent_command": "",
            "retain_requested": False,
            "retain_reason": "",
            "terminal_effect": "",
            "owner_dispatch_ids": [],
        }

    def handles_with_intended_role(
        self, intended_role: str = "phase_reviewer"
    ) -> list[str]:
        """Ledger query: every handle registered with this intended role, in order.

        Read-only: issues no Orca command and mutates nothing, so it is inert under
        the public-method sweep in test_orca_runtime_contract.py. The parameter is
        defaulted on purpose -- that sweep binds every public method by keyword.
        """
        return [
            handle
            for handle, row in self._terminals.items()
            if row["intended_role"] == intended_role
        ]

    def record_terminal_effect(self, handle: str = "", effect: str = "") -> None:
        """Store the worker-start terminal effect (created|reused) on the row.

        Kept off start_worker's return type on purpose: that tuple[str, bool] is
        unpacked at nine call sites, seven of them existing tests. Consumers read
        ledger_terminal(handle)["terminal_effect"] instead.
        """
        row = self._terminals.get(handle)
        if row is None or not effect:
            return
        row["terminal_effect"] = effect

    def reuse_chain(self, handle: str = "") -> tuple[str, ...]:
        """Every dispatch id that has owned this handle, in order. Read-only."""
        row = self._terminals.get(handle)
        return tuple(row["owner_dispatch_ids"]) if row is not None else ()

    def mark_retain_requested(
        self, handle: str = "", *, retain_reason: str = "explicit_user_request"
    ) -> None:
        """Record the user's explicit retain. The only path that sets the flag."""
        row = self._terminals.get(handle)
        if row is None:
            return
        row["retain_requested"] = True
        row["retain_reason"] = retain_reason

    def clear_retain_requested(self, handle: str = "") -> None:
        """The guide's "worker-release clears the requested retention", as code."""
        row = self._terminals.get(handle)
        if row is None:
            return
        row["retain_requested"] = False
        row["retain_reason"] = ""

    def observe_for_reuse(
        self, dispatch_id: str = "", handle: str = ""
    ) -> ReuseObservation:
        """One read-only `worker-show` for this dispatch, folded into a record.

        Exactly one command, and it is a read. Zero lifecycle mutations, zero ledger
        writes. It lives OUTSIDE reuse_eligible() on purpose: the predicate then has
        no input that could reach a stored liveness value, so axis (c1) staleness
        (documented as up to ~10s) cannot be laundered into a reuse decision. R-6 is
        closed by the signature, not by prose.

        Missing keys become "" rather than an exception, because judgement belongs in
        exactly one place -- the predicate -- and "" is already a failing value there.
        """
        observed = self.call(
            "orchestration", "worker-show", "--dispatch", dispatch_id
        )["result"]
        worker = observed.get("worker") or {}
        terminal_resource = observed.get("terminalResource") or {}
        return ReuseObservation(
            observed_at_dispatch=dispatch_id,
            handle=handle,
            worker_state=str(worker.get("state") or ""),
            release_state=str(terminal_resource.get("releaseState") or ""),
            ownership_state=str(terminal_resource.get("ownershipState") or ""),
            retained_reason=str(terminal_resource.get("retainedReason") or ""),
        )

    def reuse_eligible(
        self,
        handle: str = "",
        *,
        role: str = "",
        agent_command: str = "",
        dispatch_id: str = "",
        observation: "ReuseObservation | None" = None,
    ) -> tuple[bool, tuple[str, ...]]:
        """The eight-condition reuse gate. Returns (eligible, failure names).

        Pure with respect to the runtime: issues ZERO Orca commands and writes
        nothing. The fresh liveness look is an ARGUMENT, never something this method
        fetches or remembers -- that is what makes reusing a stale observation
        impossible rather than merely discouraged (R-6).

        Never short-circuits. Every failing condition contributes its name, so a
        negative test can bind to exactly one name, and a condition left as a
        placeholder is caught by the name that fails to appear.
        """
        reasons: list[str] = []

        # ---- 0. the observation itself ---------------------------------------
        # The sweep in test_orca_runtime_contract.py binds `observation` to a dict,
        # so a wrong type must be REFUSED, never raise.
        if not isinstance(observation, ReuseObservation):
            reasons.append("stale_or_missing_observation")
            fresh = ReuseObservation()          # all "" -> every allowlist fails
        else:
            fresh = observation
            if fresh.observed_at_dispatch != dispatch_id or fresh.handle != handle:
                reasons.append("observation_not_for_this_dispatch")

        row = self.ledger_terminal(handle)

        # ---- 1. same role -----------------------------------------------------
        if row["intended_role"] != role or role not in CLOSE_ELIGIBLE_ROLES:
            reasons.append("role_mismatch")

        # ---- 2. same agent command -------------------------------------------
        if (
            not row["agent_command"]
            or not agent_command
            or row["agent_command"] != agent_command
        ):
            reasons.append("agent_command_mismatch")

        # ---- 3. positively live (allowlists, not denylists) -------------------
        if not fresh.release_state:
            reasons.append("release_state_missing")
        elif fresh.release_state not in LIVE_RELEASE_STATES:
            reasons.append("release_state_not_live")
        if not fresh.worker_state:
            reasons.append("worker_state_missing")
        elif fresh.worker_state not in REUSABLE_WORKER_STATES:
            reasons.append("worker_state_not_reusable")

        # ---- 4. previous dispatch settled AND finalized -----------------------
        if (self._ledger.get(dispatch_id) or {}).get("state") != "finalized":
            reasons.append("previous_dispatch_not_finalized")

        # ---- 5. ownership transferable ----------------------------------------
        if fresh.ownership_state not in OWNERSHIP_TRANSFERABLE_STATES:
            reasons.append("ownership_not_transferable")
        if row["owner_dispatch_id"] != dispatch_id:
            reasons.append("ownership_not_held_by_this_dispatch")
        if not row["terminal_effect"]:
            reasons.append("terminal_effect_unrecorded")

        # ---- 6. not explicitly retained ---------------------------------------
        if row["retain_requested"] is not False:
            reasons.append("explicitly_retained")

        # ---- 7. self-created, close-eligible, not the coordinator's own -------
        if row["origin"] != "self_created":
            reasons.append("not_self_created")
        if row["role"] not in CLOSE_ELIGIBLE_ROLES:
            reasons.append("role_not_reuse_eligible")
        if handle and handle == os.environ.get(SELF_HANDLE_ENV):
            reasons.append("coordinator_self_handle")

        # ---- 8. not in lifecycle recovery -------------------------------------
        # The worker-state half of PLAN's condition 8 is condition 3's allowlist,
        # evaluated above on the same field of the same fresh record with its own
        # name. Repeating it here would emit two names for one fact and break the
        # "exactly one name" assertion the fail-closed negatives bind to.
        recovery = self.lifecycle_recovery_state(dispatch_id)
        if recovery:
            reasons.append(recovery)

        if reasons:
            # De-duplicated: lifecycle_recovery_state() answers
            # `previous_dispatch_not_finalized` for an absent settlement row, which is
            # the same fact condition 4 names. One fact, one name.
            return False, tuple(sorted(set(reasons)))
        return True, ()

    def terminal_for_next_dispatch(
        self,
        handle: str = "",
        *,
        role: str = "",
        agent_command: str = "",
        dispatch_id: str = "",
    ) -> str | None:
        """The one place a reuse decision becomes the NEXT dispatch's `terminal=`.

        reuse_eligible() is a predicate; this is its only consumer. It takes the
        fresh observation itself -- one read, for `dispatch_id`, taken here rather
        than remembered -- hands it to the gate, and returns the handle only when all
        eight conditions hold. Every other answer is None, which is exactly
        run_existing_task's fresh-terminal path: an ineligible session degrades to a
        new terminal instead of being reused on a guess (fail-closed, same direction
        as the gate's own allowlists).

        Without a consumer the gate is unreachable: a predicate nobody asks cannot
        refuse anything, and reuse would be decided by loop position instead of by
        the eight conditions (TEST-I1-MAJOR-1). No handle or no previous dispatch is
        the first attempt of a role -- there is nothing to reuse, so it is fresh
        without asking.
        """
        if not handle or not dispatch_id:
            return None
        eligible, _reasons = self.reuse_eligible(
            handle,
            role=role,
            agent_command=agent_command,
            dispatch_id=dispatch_id,
            observation=self.observe_for_reuse(
                dispatch_id=dispatch_id, handle=handle
            ),
        )
        return handle if eligible else None

    def classify_terminal(
        self,
        *,
        handle: str,
        role: str,
        origin: str,
        owned_by_this_dispatch: bool,
    ) -> dict[str, Any]:
        """Classify a hypothetical row without touching the runtime or the ledger."""
        authority = cleanup_authority(role, origin, owned_by_this_dispatch)
        return {
            "handle": handle,
            "role": role,
            "origin": origin,
            "intended_role": role,
            "owner_dispatch_id": handle if owned_by_this_dispatch else None,
            "created_by": "simulated",
            "policy_commands": [],
            "tui_idle": "unobserved",
            "cleanup_authority": authority,
            "action": "closed by coordinator" if authority == "authorized" else "retained",
        }

    def claim_settlement(
        self,
        dispatch_id: str,
        *,
        task_id: str,
        terminal: str,
        role: str,
        iteration: int,
    ) -> RuntimeAttempt | None:
        """STEP 0 gate. The ONLY entry point to a lifecycle mutation for a Dispatch.

        Returns None when the caller now owns this dispatch's settlement and may issue
        lifecycle commands. Returns the recorded RuntimeAttempt (a copy) when the
        dispatch was already finalized -- the caller must return it immediately and
        issue NO Orca command at all. Raises OrcaRuntimeError when a previous
        settlement claimed the row and never finalized it; that state is recovered
        explicitly, never re-mutated.

        The ledger row is one-way: absent -> in_progress -> finalized. There is
        deliberately NO API that moves a claimed row back to "absent"; a claim
        carries no proof of how many mutations already went out, so releasing it
        would let the next settle_attempt() pass this gate and repeat a lifecycle
        command that the runtime has already accepted.

        MUST be called before the first self.call(...) of the settlement path.
        """
        row = self._ledger.get(dispatch_id)
        if row is None:
            row = self._ledger[dispatch_id] = {
                "dispatch_id": dispatch_id,
                "task_id": task_id,
                "handle": terminal,
                "role": role,
                "iteration": iteration,
                "state": "absent",
                "replays": 0,
                "attempt": None,
            }
        if row["state"] == "absent":
            row["state"] = "in_progress"
            return None
        if row["state"] == "finalized":
            row["replays"] += 1
            return replace(row["attempt"])
        raise OrcaRuntimeError(
            f"dispatch {dispatch_id} settlement is in progress or crashed "
            "mid-settlement; recover explicitly instead of repeating the "
            "lifecycle action"
        )

    def lifecycle_recovery_state(self, dispatch_id: str = "") -> str:
        """Return "" when this dispatch is clean, else the name of what is wrong.

        Read-only. Folds the three signals that used to be scattered across an
        exception (claim_settlement raising on an in_progress row), a ledger key
        (`unsettled_reason`, written by settle_attempt's STEP 1b except branch) and a
        recorded attempt into the single answer a reuse gate needs. ANALYSIS F-7
        condition 8 asked for exactly this.
        """
        row = self._ledger.get(dispatch_id)
        if row is None:
            return "previous_dispatch_not_finalized"
        if row.get("state") == "in_progress":
            return "settlement_in_progress"
        if row.get("unsettled_reason"):
            return "previous_dispatch_unsettled"
        attempt = row.get("attempt")
        if attempt is not None and (
            attempt.worker_state in UNSETTLED_WORKER_STATES
            or attempt.outcome == "unknown"
        ):
            return "previous_attempt_in_recovery"
        return ""

    def verify_settlement(
        self,
        dispatch_id: str,
        *,
        task_id: str,
        observation: dict[str, Any],
        done: dict[str, Any],
        task_status: str,
        supervised: bool,
    ) -> str:
        """STEP 1b gate. Prove axis (a) BEFORE the first lifecycle mutation.

        Pure with respect to the runtime: issues ZERO Orca commands, exactly like
        account_axes(). Every input was already fetched by the read-only STEP 1/1b
        observations, so proving settlement costs no extra mutation risk.

        Returns the proven Dispatch status ("completed" or "failed") and lets the
        caller proceed to STEP 2. Raises OrcaRuntimeError -- with no lifecycle
        command issued at all -- when this Dispatch did not actually settle:

          * the `worker_done` was rejected by the runtime;
          * the `worker_done` does not carry BOTH expected identities, or carries the
            wrong one (it settles a different Dispatch or a different Task than the
            one we are about to mutate);
          * the `worker_done` carries no explicit `succeeded`/`failed` outcome, so it
            is not an accepted settlement message at all;
          * the supervised worker record produced no outcome (UNSETTLED_WORKER_STATES);
          * the Dispatch row or the Task row is still `dispatched`;
          * the settled Dispatch row carries no completion timestamp, so its
            provenance does not actually record a completion.

        Those are exactly the cases SKILL.md section 6 axis (a) routes to the
        recovery path (observe_unexpected_exit's abandon/task-update flow), never to
        worker-release / worker-retain / close. STEP 0's exactly-once gate answers a
        different question -- "did I already settle this Dispatch?" -- and stays
        ahead of this check; this one answers "is there a settlement to account at
        all?".

        Every one of these is a *read-only* question, which is the whole reason they
        belong here: a check that could be answered before the mutation and is
        instead answered after it (STEP 4's payload["outcome"], before this gate
        validated the field) is the same defect in miniature.
        """
        payload = json.loads(done["payload"])
        if payload.get("_orcaLifecycleRejection"):
            raise OrcaRuntimeError(
                f"dispatch {dispatch_id} worker_done was rejected by Orca; no "
                "lifecycle mutation issued -- follow the guide's recovery procedure"
            )
        # Identity before everything else that reads the payload: a message that does
        # not provably belong to THIS dispatch and THIS task says nothing about them,
        # whatever else it contains. dispatchId is checked first so a mismatched
        # dispatch keeps reporting itself as a stale delivery.
        expected = {"dispatchId": dispatch_id, "taskId": task_id}
        for field_name in WORKER_DONE_IDENTITY_FIELDS:
            reported = payload.get(field_name)
            if reported is None:
                raise OrcaRuntimeError(
                    f"worker_done for dispatch {dispatch_id} carries no "
                    f"{field_name}; its identity cannot be proven and no lifecycle "
                    "mutation was issued"
                )
            if reported != expected[field_name]:
                raise OrcaRuntimeError(
                    f"stale worker_done: payload {field_name} is {reported}, not "
                    f"{expected[field_name]}; no lifecycle mutation issued"
                )
        outcome = payload.get("outcome")
        if outcome not in SETTLED_OUTCOMES:
            raise OrcaRuntimeError(
                f"dispatch {dispatch_id} worker_done carries outcome {outcome!r}, "
                f"not one of {sorted(SETTLED_OUTCOMES)}; no lifecycle mutation "
                "issued -- an accepted worker_done reports an explicit outcome, so "
                "recover this dispatch explicitly instead"
            )
        worker_state = (observation.get("worker") or {}).get("state")
        if supervised and worker_state in UNSETTLED_WORKER_STATES:
            raise OrcaRuntimeError(
                f"dispatch {dispatch_id} worker is {worker_state!r} and produced no "
                "outcome; no lifecycle mutation issued -- take the abandon recovery "
                "path instead"
            )
        dispatch_row = observation.get("dispatch") or {}
        dispatch_status = dispatch_row.get("status")
        unsettled = ", ".join(
            f"{name} status {status!r}"
            for name, status in (("dispatch", dispatch_status), ("task", task_status))
            if status not in SETTLED_STATUSES
        )
        if unsettled:
            raise OrcaRuntimeError(
                f"dispatch {dispatch_id} is not settled ({unsettled}); no lifecycle "
                "mutation issued -- axis (a) must be proven from Task/Dispatch "
                "provenance before worker-release/worker-retain, so recover this "
                "dispatch explicitly instead"
            )
        # The last half of the axis (a) sentence: a settled status AND a completion
        # timestamp in the provenance. A row that claims an outcome but records no
        # moment of completion is not the settlement receipt the guide asks for.
        if completion_timestamp(dispatch_row) is None:
            raise OrcaRuntimeError(
                f"dispatch {dispatch_id} reports status {dispatch_status!r} but its "
                "provenance carries no completion timestamp; no lifecycle mutation "
                "issued -- axis (a) requires both before worker-release/worker-retain"
            )
        return dispatch_status

    def account_axes(
        self,
        task_id: str,
        dispatch_id: str,
        terminal: str,
        *,
        supervised: bool,
        observation: dict[str, Any],
        task_status: str,
        lifecycle: str,
        release_process_action: str = "",
    ) -> tuple[str, str, str, str, str]:
        """Return (settlement, worker_resource, process_liveness, cleanup, role).

        Pure with respect to the runtime: issues ZERO Orca commands. Every input is
        either already-fetched observation data, the ledger, or the caller's choice.
        """
        if lifecycle not in LIFECYCLE_INTENTS:
            raise OrcaRuntimeError(f"unknown lifecycle intent: {lifecycle}")
        settlement = (
            task_status if task_status in {"completed", "failed"} else "not-settled"
        )
        if supervised:
            worker_resource = lifecycle
            terminal_resource = observation.get("terminalResource") or {}
            if not terminal_resource:
                process_liveness = "disputed"
            elif terminal_resource.get("releaseState") in {
                "released",
                "closed",
                "exited",
            }:
                process_liveness = "already exited"
            else:
                process_liveness = "live"
        else:
            worker_resource = "unsupervised"
            observed = observation.get("terminalState")
            if observed in {"exited", "released", "closed"}:
                process_liveness = "already exited"
            elif observed == "reused":
                process_liveness = "live"
            else:
                process_liveness = "disputed"

        row = self.ledger_terminal(terminal)
        authority = cleanup_authority(
            row["role"], row["origin"], row["owner_dispatch_id"] == dispatch_id
        )
        # Order rule: close is only ever decided while the process is live.
        #
        # The retain-intent gate sits ABOVE the authority gate on purpose. Axis (b)
        # records what happened to the worker *resource* ("unsupervised" whenever no
        # supervised resource was ever registered), while `lifecycle` records what the
        # coordinator decided about the *terminal*. reuse and retain both keep the
        # terminal alive for its next owner, so proven cleanup authority is exactly the
        # case in which a close would be possible and still must not happen.
        if process_liveness != "live":
            action = "nothing to do"
        elif lifecycle in RETAIN_INTENTS:
            action = "retained"
        elif authority != "authorized":
            action = "retained"
        elif worker_resource == "unsupervised":
            action = "closed by coordinator"
        elif not release_process_action:
            # No receipt was supplied: keep the pre-existing label rather than invent
            # a downgrade from missing evidence. The settlement path always supplies
            # one; this default is what keeps AxisMatrixTests unmodified.
            action = "released by runtime"
        elif release_process_action in PROCESS_TERMINATING_ACTIONS:
            action = "released by runtime"
        else:
            # D-6 / R8-iii: a release receipt that does not prove a termination means
            # the runtime kept the process, whatever cleanup authority said.
            action = "retained (runtime kept the process)"
        if terminal in self._terminals:
            self._terminals[terminal]["cleanup_authority"] = authority
            self._terminals[terminal]["action"] = action
        return (
            settlement,
            worker_resource,
            process_liveness,
            authority,
            row["role"],
        )

    def finalize_once(
        self, dispatch_id: str, *, attempt: RuntimeAttempt, **axes: str
    ) -> dict[str, Any]:
        """Single-assignment writer for a claimed row. Never call without claim."""
        row = self._ledger.get(dispatch_id)
        if row is None:
            raise OrcaRuntimeError(
                f"dispatch {dispatch_id} was never claimed; call claim_settlement first"
            )
        if row["state"] == "finalized":
            raise OrcaRuntimeError(f"dispatch {dispatch_id} was already finalized")
        row.update(axes)
        row["attempt"] = attempt
        row["state"] = "finalized"
        return row

    def lifecycle_commands(
        self, dispatch_id: str | None = None, handle: str | None = None
    ) -> list[str]:
        """Lifecycle-mutating Orca commands actually executed, derived from self._raw.

        Never a hand-maintained counter, so it cannot drift from what was really sent.
        Execution order is preserved and duplicates are not collapsed: an equality
        assertion against this list therefore counts mutations, not just presence.
        """
        verbs: list[str] = []
        for row in self._raw:
            args = row["command"]
            verb = args[1] if len(args) > 1 else args[0]
            if verb not in LIFECYCLE_MUTATION_COMMANDS:
                continue
            if dispatch_id is not None and _flag_value(args, "--dispatch") != dispatch_id:
                continue
            if handle is not None and _flag_value(args, "--terminal") != handle:
                continue
            verbs.append(verb)
        return verbs

    def preflight(self) -> dict[str, Any]:
        status = self.call("status")["result"]
        if status["runtime"]["state"] != "ready":
            raise OrcaRuntimeError("Orca runtime is not ready")
        orchestration = subprocess.run(
            [self.orca, "skills", "get", "orchestration"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        cli = subprocess.run(
            [self.orca, "skills", "get", "orca-cli"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        validate_orca_contract(status["runtime"]["appVersion"], orchestration, cli)
        current = self.call("worktree", "current")
        return {
            "executable": self.orca,
            "appVersion": status["runtime"]["appVersion"],
            "runtimeId": status["runtime"]["runtimeId"],
            "worktreeId": current["result"]["worktree"]["id"],
            "guides": {
                "orchestration": "orca skills get orchestration",
                "orca-cli": "orca skills get orca-cli",
                "orcaCliGuideLoaded": "terminal create" in cli,
            },
        }

    def start_run(self, objective: str) -> str:
        terminal = self.call(
            "terminal", "create", "--worktree", "current", "--title", objective, "--command", "bash"
        )
        self.run_owner = terminal["result"]["terminal"]["handle"]
        self.register_terminal(
            self.run_owner, role="run_owner_fixture", origin="self_created"
        )
        created = self.call(
            "orchestration", "run-create", "--objective", objective, "--from", self.run_owner
        )
        self.run_id = created["result"]["run"]["id"]
        self._signals = []
        self._ledger = {}
        return self.run_id

    def create_task(self, spec: str, *, deps: tuple[str, ...] = ()) -> str:
        assert self.run_owner
        args = ["orchestration", "task-create", "--spec", spec]
        if deps:
            args.extend(["--deps", json.dumps(list(deps))])
        args.extend(["--from", self.run_owner])
        created = self.call(*args)
        return created["result"]["task"]["id"]

    def task_status(self, task_id: str) -> str:
        """Read one task's status from the run's task listing."""
        tasks = self.call("orchestration", "task-list", "--run", self.run_id)["result"][
            "tasks"
        ]
        task = next((item for item in tasks if item["id"] == task_id), None)
        if task is None:
            raise OrcaRuntimeError(f"task {task_id} is not part of run {self.run_id}")
        return task["status"]

    def create_fake_terminal(
        self,
        role: str,
        mode: str,
        *,
        iteration: int,
        findings: tuple[str, ...] = (),
        resolutions: dict[str, str] | None = None,
        max_dispatches: int = 1,
        ask_before: bool = False,
    ) -> str:
        command = [
            "exec",
            str(FAKE_CODEX),
            "--role",
            role,
            "--mode",
            mode,
            "--iteration",
            str(iteration),
            "--findings-json",
            json.dumps(findings),
            "--resolutions-json",
            json.dumps(resolutions or {}, sort_keys=True),
            "--max-dispatches",
            str(max_dispatches),
            "--orca-command",
            self.orca,
        ]
        if ask_before:
            command.append("--ask-before")
        agent_command = shlex.join(command)
        created = self.call(
            "terminal",
            "create",
            "--worktree",
            "current",
            "--title",
            f"fake-{role}-{iteration}",
            "--command",
            agent_command,
        )
        handle = created["result"]["terminal"]["handle"]
        self.register_terminal(
            handle,
            role="active_worker",
            origin="self_created",
            intended_role="phase_reviewer"
            if role.endswith("reviewer")
            else "phase_worker",
            agent_command=agent_command,  # W-20: the reuse gate's condition 2 evidence
        )
        return handle

    def wait_for_tui_idle(self, terminal: str) -> str:
        """Middle rung of the custom-command placement ladder (SKILL.md section 6).

        Rung 3 is `terminal create` -> wait until the TUI is idle -> `worker-start
        --terminal <handle>`. The wait is not decoration: worker-start adopts whatever
        the terminal currently is, so skipping it hands the runtime a process that may
        still be painting its startup UI and has not yet reached its prompt.

        A wait that cannot confirm idle is recorded and adoption still proceeds --
        rung 3 descends to rung 4 only when worker-start itself reports an
        unconfigured agent, never because an observation was inconclusive.
        """
        waited = self.call(
            "terminal",
            "wait",
            "--terminal",
            terminal,
            "--for",
            "tui-idle",
            "--timeout-ms",
            str(self.wait_timeout_ms),
            allow_error=True,
        )
        if not waited.get("ok"):
            state = "unobserved"
        elif ((waited.get("result") or {}).get("wait") or {}).get("satisfied"):
            state = "idle"
        else:
            state = "timeout"
        row = self._terminals.get(terminal)
        if row is not None:
            row["tui_idle"] = state
        return state

    def start_worker(self, task_id: str, terminal: str, spec: str) -> tuple[str, bool]:
        assert self.run_owner
        if terminal == os.environ.get(SELF_HANDLE_ENV):
            raise OrcaRuntimeError(
                "refusing to register the caller's own terminal as a worker resource"
            )
        # Ladder rung 3, in order: the terminal already exists, so idle first, adopt
        # second. Both steps precede any dispatch, so rung 4 can never run ahead of it.
        self.wait_for_tui_idle(terminal)
        started = self.call(
            "orchestration",
            "worker-start",
            "--task",
            task_id,
            "--terminal",
            terminal,
            "--from",
            self.run_owner,
            allow_error=True,
        )
        if started.get("ok"):
            result = started["result"]
            dispatch_id = result["dispatchId"]
            self._attach_terminal(terminal, dispatch_id, "supervised_adopted")
            # W-21. Deliberately NOT widened into the return type: tuple[str, bool] is
            # unpacked at nine call sites, seven of them existing tests. Consumers read
            # ledger_terminal(handle)["terminal_effect"] instead.
            self.record_terminal_effect(
                terminal, worker_start_terminal_effect(result)
            )
            return dispatch_id, True
        error = started.get("error", {})
        # Only agent_unconfigured is a branch signal; every other error is a real
        # failure (SKILL.md section 6 Custom command handling, rule 1).
        if error.get("code") != "agent_unconfigured":
            raise OrcaRuntimeError(f"worker-start failed: {error}")
        dispatched = self.call(
            "orchestration",
            "dispatch",
            "--task",
            task_id,
            "--to",
            terminal,
            "--from",
            self.run_owner,
        )
        dispatch_id = dispatched["result"]["dispatch"]["id"]
        prompt = (
            f"taskId: {task_id}\n"
            f"dispatchId: {dispatch_id}\n"
            "Use worker_done exactly once with an explicit outcome.\n"
            "=== TASK ===\n"
            f"{spec}"
        )
        self.call(
            "terminal", "send", "--terminal", terminal, "--text", prompt, "--enter"
        )
        self._attach_terminal(terminal, dispatch_id, "low_level_tracked")
        return dispatch_id, False

    def _check(self) -> dict[str, Any]:
        assert self.run_owner
        return self.call(
            "orchestration",
            "check",
            "--terminal",
            self.run_owner,
            "--wait",
            "--types",
            WAIT_TYPES,
            "--timeout-ms",
            str(self.wait_timeout_ms),
        )["result"]

    def _ack(self, delivery_id: str) -> None:
        assert self.run_owner
        self.call(
            "orchestration", "check", "--terminal", self.run_owner, "--ack", delivery_id
        )

    def confirm_terminal_exit(self, terminal: str) -> str:
        waited = self.call(
            "terminal",
            "wait",
            "--terminal",
            terminal,
            "--for",
            "exit",
            "--timeout-ms",
            str(self.wait_timeout_ms),
            allow_error=True,
        )
        if not waited.get("ok"):
            message = (waited.get("error") or {}).get("message")
            if message == "tab_not_found":
                return "exited"
            raise OrcaRuntimeError(f"terminal exit observation failed: {waited.get('error')}")
        if not waited["result"]["wait"]["satisfied"]:
            raise OrcaRuntimeError("fake terminal did not exit after settlement")
        return "exited"

    def wait_for_done(self, dispatch_id: str) -> tuple[dict[str, Any], str]:
        while True:
            delivery = self._check()
            if delivery.get("timedOut") or not delivery.get("messages"):
                raise OrcaRuntimeError(f"timed out waiting for Dispatch {dispatch_id}")
            done = None
            for message in delivery["messages"]:
                message_type = message["type"]
                self._signals.append(message_type)
                if message_type == "question":
                    self.call(
                        "orchestration",
                        "reply",
                        "--id",
                        message["id"],
                        "--body",
                        "yes",
                        "--from",
                        self.run_owner,
                    )
                elif message_type == "escalation":
                    pass
                elif message_type == "worker_done":
                    payload = json.loads(message["payload"])
                    if payload.get("dispatchId") == dispatch_id:
                        if done is not None:
                            raise OrcaRuntimeError("worker_done was delivered more than once")
                        if payload.get("_orcaLifecycleRejection"):
                            raise OrcaRuntimeError("worker_done was rejected by Orca")
                        done = message
            if done is not None:
                return done, delivery["deliveryId"]
            self._ack(delivery["deliveryId"])

    def settle_attempt(
        self,
        role: str,
        iteration: int,
        task_id: str,
        dispatch_id: str,
        done: dict[str, Any],
        delivery_id: str,
        *,
        lifecycle: str = "release",
        supervised: bool = True,
        terminal: str,
        retain_reason: str = "explicit_user_request",
    ) -> RuntimeAttempt:
        # ==== STEP 0. FINALIZATION GATE =====================================
        # The first statement of the function. Nothing above it, and in particular no
        # self.call(...), may run before it. A replayed settlement returns here having
        # issued zero Orca commands.
        recorded = self.claim_settlement(
            dispatch_id,
            task_id=task_id,
            terminal=terminal,
            role=role,
            iteration=iteration,
        )
        if recorded is not None:
            return recorded

        # ==== STEP 1. read-only observation =================================
        if supervised:
            observation = self.call(
                "orchestration", "worker-show", "--dispatch", dispatch_id
            )["result"]
        else:
            shown = self.call("orchestration", "dispatch-show", "--task", task_id)[
                "result"
            ]
            observation = {"dispatch": shown.get("dispatch") or shown}

        # ==== STEP 1b. SETTLEMENT VERIFICATION ==============================
        # Axis (a), proven from real Task/Dispatch provenance and proven HERE, above
        # every lifecycle mutation. Both reads are read-only: STEP 1 already fetched
        # the Dispatch row, and task_status() reads the same Task row STEP 4 accounts
        # from -- it is read earlier now, not read twice. A dispatch that never
        # settled leaves this method by raising, having issued zero lifecycle
        # commands, and is recovered explicitly. The EXPECTED task id is handed in so
        # the gate can compare both identities the guide names, not just the dispatch.
        task_status = self.task_status(task_id)
        try:
            verified_status = self.verify_settlement(
                dispatch_id,
                task_id=task_id,
                observation=observation,
                done=done,
                task_status=task_status,
                supervised=supervised,
            )
        except OrcaRuntimeError as error:
            # The STEP 0 claim is one-way and stays in place; record why it was
            # refused so the recovery path finds a reason, not a bare stuck row.
            self._ledger[dispatch_id]["unsettled_reason"] = str(error)
            raise

        # ==== STEP 2. exactly one lifecycle mutation ========================
        # The only place in settle_attempt that mutates lifecycle state. It is
        # unreachable unless STEP 0 handed this dispatch to us AND STEP 1b proved the
        # dispatch actually settled.
        dispatch_status = verified_status
        if supervised:
            command = LIFECYCLE_TO_COMMAND.get(lifecycle)
            worker_state = observation["worker"]["state"]
            terminal_resource = observation.get("terminalResource") or {}
            terminal_state = terminal_resource.get("releaseState", "none")
            if command is None:
                # reuse: ZERO lifecycle mutations. Ownership moves when the next Task
                # is started on this same terminal; nothing is sent to THIS dispatch.
                # `observation` was already fetched read-only in STEP 1, so this
                # branch needs no extra command to fill worker_state/terminal_state.
                lifecycle_action = "reuse:ownership-transfer-pending"
                release_process_action = ""
            else:
                action = self.call(
                    "orchestration", command, "--dispatch", dispatch_id
                )
                lifecycle_action = f"{lifecycle}:{action['result']['state']}"
                release_process_action = action["result"].get("processAction", "")
                if lifecycle == "retain":  # W-37 set
                    self.mark_retain_requested(
                        terminal, retain_reason=retain_reason
                    )
                else:  # W-37 clear
                    self.clear_retain_requested(terminal)
        else:
            worker_state = "settled_external"
            terminal_state = (
                "reused"
                if lifecycle in {"retain", "reuse"}
                else self.confirm_terminal_exit(terminal)
            )
            lifecycle_action = (
                "reuse:tracked-external"
                if lifecycle in {"retain", "reuse"}
                else "release:natural-exit"
            )
            observation["terminalState"] = terminal_state
            release_process_action = ""

        # ==== STEP 3. delivery ack ==========================================
        self._ack(delivery_id)
        # Safe to index: STEP 1b proved this payload carries an explicit outcome from
        # SETTLED_OUTCOMES, above the mutation, so this read can no longer be the
        # first place a malformed worker_done is noticed.
        payload = json.loads(done["payload"])

        # ==== STEP 4. axes + single-assignment finalization =================
        # The single allowed upward role transition, applied only after axis (a) has
        # confirmed a real completion for this dispatch.
        self.demote_or_promote_role(
            terminal,
            self.ledger_terminal(terminal)["intended_role"],
            settled=task_status == "completed",
        )
        axes = self.account_axes(
            task_id,
            dispatch_id,
            terminal,
            supervised=supervised,
            observation=observation,
            task_status=task_status,
            lifecycle=lifecycle,
            release_process_action=release_process_action,  # W-16 -> W-29
        )
        attempt = RuntimeAttempt(
            role=role,
            iteration=iteration,
            task_id=task_id,
            dispatch_id=dispatch_id,
            outcome=payload["outcome"],
            task_status=task_status,
            dispatch_status=dispatch_status,
            worker_state=worker_state,
            terminal_state=terminal_state,
            lifecycle_action=lifecycle_action,
            worker_done_count=1,
            execution_path="supervised" if supervised else "tracked_external",
            body=done["body"],
            settlement=axes[0],
            worker_resource=axes[1],
            process_liveness=axes[2],
            cleanup_authority=axes[3],
            terminal_role=axes[4],
            finalizations=1,
            terminal=terminal,
            terminal_effect=self.ledger_terminal(terminal)["terminal_effect"],
            release_process_action=release_process_action,
        )
        self.finalize_once(
            dispatch_id,
            attempt=attempt,
            settlement=axes[0],
            worker_resource=axes[1],
            process_liveness=axes[2],
            cleanup_authority=axes[3],
            terminal_role=axes[4],
        )
        return attempt

    def run_existing_task(
        self,
        role: str,
        iteration: int,
        mode: str,
        task_id: str,
        *,
        phase: str | None = None,
        spec: str | None = None,
        findings: tuple[str, ...] = (),
        resolutions: dict[str, str] | None = None,
        evidence: WorkflowEvidence | None = None,
        ask_before: bool = False,
        lifecycle: str = "release",
        terminal: str | None = None,
        max_dispatches: int = 1,
    ) -> tuple[RuntimeAttempt, str]:
        """Dispatch and settle a Task that already exists (the graph-first path).

        Identical to run_attempt except that the Task is NOT created here.

        `phase` is the workflow stage and is passed straight through to
        dispatch_context, which refuses a missing or unknown one. A caller that
        rendered the spec itself must pass the SAME phase and evidence it rendered
        with: this re-render is the text that actually reaches worker-start, so a
        caller that supplied one and not the other would dispatch a different
        boundary than the one it created the Task with.
        """
        # Before the dispatch, not after it. `spec` is what start_worker sends on the
        # low-level path and what a caller passes to task-create on the supervised
        # one, so the boundary has to be inside it by the time either happens.
        spec, boundary, reviewer_context = dispatch_context(
            role,
            iteration,
            mode,
            phase=phase,
            base_spec=spec,
            findings=findings,
            resolutions=resolutions,
            evidence=evidence,
            run_id=self.run_id or "",
        )
        created_here = terminal is None
        handle = terminal or self.create_fake_terminal(
            role,
            mode,
            iteration=iteration,
            findings=findings,
            resolutions=resolutions,
            max_dispatches=max_dispatches,
            ask_before=ask_before,
        )
        dispatch_id, supervised = self.start_worker(task_id, handle, spec)
        done, delivery_id = self.wait_for_done(dispatch_id)
        attempt = self.settle_attempt(
            role,
            iteration,
            task_id,
            dispatch_id,
            done,
            delivery_id,
            lifecycle=lifecycle,
            supervised=supervised,
            terminal=handle,
        )

        # W-27. The one wiring point that makes test N observable on a single attempt
        # list: the same object carries (a) the handle it kept, (b) the new
        # task/dispatch identity, (c) the refreshed layer-1 boundary and (d) the
        # Reviewer delta context. RuntimeAttempt is a plain (non-frozen) dataclass, so
        # these are assigned after settlement rather than widening its constructor.
        # The two payloads are the objects that were rendered into `spec` above, not
        # a second build: the record and the dispatched input cannot drift apart.
        attempt.terminal = handle
        attempt.terminal_created = created_here
        attempt.terminal_effect = self.ledger_terminal(handle)["terminal_effect"]
        attempt.task_boundary = tuple(sorted(boundary.items()))
        if reviewer_context is not None:
            attempt.reviewer_context_keys = tuple(sorted(reviewer_context))
        return attempt, handle

    def run_attempt(
        self,
        role: str,
        iteration: int,
        mode: str,
        *,
        phase: str | None = None,
        findings: tuple[str, ...] = (),
        resolutions: dict[str, str] | None = None,
        evidence: WorkflowEvidence | None = None,
        ask_before: bool = False,
        lifecycle: str = "release",
        terminal: str | None = None,
        max_dispatches: int = 1,
    ) -> tuple[RuntimeAttempt, str]:
        """Create the Task, then run it. Return type and behavior unchanged.

        The two new keyword-only parameters both default, so every existing keyword
        call site still binds; `phase` then fails closed inside dispatch_context
        rather than falling back to `mode`.
        """
        # The Task spec is write-once, and on the supervised path it is the ONLY text
        # the agent sees (Orca replays it into the preamble), so it is composed here
        # rather than in run_existing_task, which meets an already-created Task.
        spec, _, _ = dispatch_context(
            role,
            iteration,
            mode,
            phase=phase,
            findings=findings,
            resolutions=resolutions,
            evidence=evidence,
            run_id=self.run_id or "",
        )
        task_id = self.create_task(spec)
        return self.run_existing_task(
            role,
            iteration,
            mode,
            task_id,
            phase=phase,
            spec=spec,
            findings=findings,
            resolutions=resolutions,
            evidence=evidence,
            ask_before=ask_before,
            lifecycle=lifecycle,
            terminal=terminal,
            max_dispatches=max_dispatches,
        )

    def observe_unexpected_exit(
        self, role: str, iteration: int, *, phase: str | None = None
    ) -> RuntimeAttempt:
        spec, _, _ = dispatch_context(
            role,
            iteration,
            "exit",
            phase=phase,
            base_spec=f"{role} iteration {iteration}: unexpected exit",
            run_id=self.run_id or "",
        )
        task_id = self.create_task(spec)
        handle = self.create_fake_terminal(role, "exit", iteration=iteration)
        dispatch_id, supervised = self.start_worker(task_id, handle, spec)
        assert self.run_owner
        # Same STEP 0 gate as settle_attempt: this path also issues worker-abandon and
        # worker-release, so no lifecycle mutation may run before the claim.
        recorded = self.claim_settlement(
            dispatch_id,
            task_id=task_id,
            terminal=handle,
            role=role,
            iteration=iteration,
        )
        if recorded is not None:
            return recorded
        self.confirm_terminal_exit(handle)
        checkpoint = self._check()
        if checkpoint.get("messages"):
            raise OrcaRuntimeError("unexpected exit produced a lifecycle message")
        if supervised:
            shown = self.call("orchestration", "worker-show", "--dispatch", dispatch_id)["result"]
            state = shown["worker"]["state"]
        else:
            self.call(
                "orchestration",
                "task-update",
                "--id",
                task_id,
                "--status",
                "failed",
                "--result",
                json.dumps({"reason": "process_exited_without_worker_done"}),
                "--from",
                self.run_owner,
            )
            shown_result = self.call("orchestration", "dispatch-show", "--task", task_id)["result"]
            shown = {"dispatch": shown_result.get("dispatch") or shown_result}
            state = "outcome_unknown_external"
        recovery = "task-update:failed" if not supervised else "observed"
        if supervised and state in UNSETTLED_WORKER_STATES:
            recovery_result = self.call(
                "orchestration", "worker-abandon", "--dispatch", dispatch_id
            )
            recovery = f"abandon:{recovery_result['result']['state']}"
            shown = self.call("orchestration", "worker-show", "--dispatch", dispatch_id)["result"]
        elif supervised and state not in {"failed", "stopped"}:
            raise OrcaRuntimeError(f"unexpected exit left worker in {state}")
        release_state = "natural-exit"
        release_process_action = ""
        if supervised:
            release = self.call("orchestration", "worker-release", "--dispatch", dispatch_id)
            release_state = release["result"]["state"]
            release_process_action = release["result"].get("processAction", "")
        tasks = self.call("orchestration", "task-list", "--run", self.run_id)["result"]["tasks"]
        task = next(item for item in tasks if item["id"] == task_id)
        # No role promotion here: this dispatch never produced an accepted worker_done,
        # so the terminal stays active_worker and therefore stays never-close.
        observation = dict(shown)
        observation["terminalState"] = "exited"
        axes = self.account_axes(
            task_id,
            dispatch_id,
            handle,
            supervised=supervised,
            observation=observation,
            task_status=task["status"],
            lifecycle="release",
            # A call site that holds a receipt always hands it over, without
            # exception -- even here, where axis (c1) is `already exited` and the
            # action falls through to "nothing to do" before the receipt is read.
            release_process_action=release_process_action,
        )
        attempt = RuntimeAttempt(
            role=role,
            iteration=iteration,
            task_id=task_id,
            dispatch_id=dispatch_id,
            outcome="unknown",
            task_status=task["status"],
            dispatch_status=shown["dispatch"]["status"],
            worker_state=state,
            terminal_state=(shown.get("terminalResource") or {}).get("releaseState", "natural_exit"),
            lifecycle_action=f"{recovery};release:{release_state}",
            worker_done_count=0,
            execution_path="supervised" if supervised else "tracked_external",
            settlement=axes[0],
            worker_resource=axes[1],
            process_liveness=axes[2],
            cleanup_authority=axes[3],
            terminal_role=axes[4],
            finalizations=1,
            terminal=handle,
            terminal_effect=self.ledger_terminal(handle)["terminal_effect"],
            release_process_action=release_process_action,
        )
        self.finalize_once(
            dispatch_id,
            attempt=attempt,
            settlement=axes[0],
            worker_resource=axes[1],
            process_liveness=axes[2],
            cleanup_authority=axes[3],
            terminal_role=axes[4],
        )
        return attempt

    def finish(self, result: RuntimeScenarioResult) -> RuntimeScenarioResult:
        assert self.run_id and self.run_owner
        result.signals = list(self._signals)
        result.run_owner_handle = self.run_owner
        for handle, row in self._terminals.items():
            row["policy_commands"] = self.lifecycle_commands(handle=handle)
        result.ledger = [dict(row) for row in self._terminals.values()] + list(
            result.ledger
        )
        # Derived from the real command log: "<group> <verb>", sorted and de-duplicated.
        # Answers "what ran / what never ran", unlike lifecycle_commands() which counts.
        result.commands_used = sorted(
            {" ".join(command["command"][:2]) for command in self._raw}
        )
        # ---- reuse aggregates (W-25). Computed BEFORE self._terminals is cleared
        # at the end of this method (A-6): once the ledger is gone the chains are
        # unrecoverable, so the aggregation cannot be deferred to a caller.
        result.terminal_creations = sum(
            1
            for row in self._terminals.values()
            if row["origin"] == "self_created" and row["role"] not in HARNESS_ONLY_ROLES
        )
        result.reuse_chains = {
            handle: list(row["owner_dispatch_ids"])
            for handle, row in self._terminals.items()
            if len(row["owner_dispatch_ids"]) > 1
        }
        # Evidence order (D-4): the runtime's own receipts first. A terminal counts as
        # retained when its recorded release receipt did not prove a termination --
        # never because the ledger's `action` label says so (ANALYSIS F-3 result 2).
        result.retained_terminals = sorted(
            handle
            for handle, row in self._terminals.items()
            if row["role"] not in HARNESS_ONLY_ROLES
            and not self._release_terminated_process(handle)
        )
        teardown_receipt = self._teardown_fixture_terminal()
        result.fixture_teardown = {**result.fixture_teardown, **teardown_receipt}
        snapshot = {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "result": asdict(result),
            "run": self.call("orchestration", "run-show", "--id", self.run_id)["result"],
            "tasks": self.call("orchestration", "task-list", "--run", self.run_id)["result"],
            "commands": self._raw,
            "fixtureTeardown": teardown_receipt,
        }
        path = self.artifact_dir / f"scenario-{result.scenario.lower()}.json"
        path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.run_owner = None
        self.run_id = None
        self._raw = []
        self._signals = []
        self._terminals = {}
        self._ledger = {}
        return result

    def _release_terminated_process(self, handle: str) -> bool:
        """Did a recorded release/retain receipt prove this handle's process ended?

        Reads the raw command log, not the ledger's `action` label, so the answer is
        the runtime's own receipt rather than this harness's accounting of it. Private
        on purpose: it is evidence plumbing for finish(), not a public judgement.
        """
        owning = set(self._terminals.get(handle, {}).get("owner_dispatch_ids") or ())
        owner = (self._terminals.get(handle) or {}).get("owner_dispatch_id")
        if owner:
            owning.add(owner)
        for row in self._raw:
            args = row["command"]
            verb = args[1] if len(args) > 1 else args[0]
            if verb not in {"worker-release", "worker-retain"}:
                continue
            dispatch_id = _flag_value(args, "--dispatch")
            if dispatch_id is not None and dispatch_id not in owning:
                continue
            result = (row.get("response") or {}).get("result") or {}
            if result.get("processAction") in PROCESS_TERMINATING_ACTIONS:
                return True
        return False

    def _teardown_fixture_terminal(self, handle: str | None = None) -> dict[str, Any]:
        """Fixture teardown, NOT the lifecycle policy.

        The policy path (account_axes / settle_attempt / finalize_once) never closes
        anything on the basis of self-creation; run_owner_fixture is a member of
        NEVER_CLOSE_ROLES and always classifies as not_authorized / retained. This
        method exists only so the harness reclaims the fixture terminal it created,
        and it refuses loudly whenever the assumption that makes that safe fails.
        """
        target = handle or self.run_owner
        if target is None:
            return {"handle": None, "selfHandleGuard": "no-fixture"}
        # GUARD 1 (first, and unconditional): never the caller's own terminal.
        self_handle = os.environ.get(SELF_HANDLE_ENV)
        if self_handle and target == self_handle:
            raise OrcaRuntimeError("refusing to close the caller's own terminal")
        # GUARD 2: the row must be the fixture we created ourselves.
        row = self.ledger_terminal(target)
        if row["role"] != "run_owner_fixture" or row["origin"] != "self_created":
            raise OrcaRuntimeError(
                f"refusing teardown of {target}: role={row['role']}"
            )
        # GUARD 3: the policy path must never have marked this handle closable.
        if close_allowed(row["role"], row["origin"], True):
            raise OrcaRuntimeError("policy path must never authorize the fixture terminal")
        # Snapshot taken BEFORE the close, so it answers the question the ledger's
        # own policy_commands column cannot: did anything close this handle before
        # teardown reached it? An empty list here plus close="issued" is the proof
        # that the only close in the whole run came from this method.
        receipt = {
            "handle": target,
            "role": row["role"],
            "origin": row["origin"],
            "selfHandleGuard": "passed" if self_handle else "unset",
            "policyCommandsBeforeTeardown": self.lifecycle_commands(handle=target),
        }
        self.call("terminal", "close", "--terminal", target, allow_error=True)
        if target == self.run_owner:
            self.run_owner = None
        receipt["close"] = "issued"
        return receipt


# Scenarios A-J exercise the LIFECYCLE inside a single workflow phase, so they name
# that phase explicitly. Naming it is the point: current_phase is never inferred from
# the fake agent's mode, not even when a scenario only cares about the mode.
LIFECYCLE_SCENARIO_PHASE = "implementation"
# Scenario K's run objective, and therefore the ORIGINAL objective its Reviewers are
# told about: the request the whole chain exists to satisfy, not the one-line spec of
# whichever attempt is being dispatched.
SESSION_REUSE_OBJECTIVE = "Session reuse scenario K five-phase same-role chain"


def run_runtime_scenarios(artifact_dir: Path) -> list[RuntimeScenarioResult]:
    harness = OrcaRuntimeHarness(artifact_dir)
    preflight = harness.preflight()
    (artifact_dir / "environment.json").write_text(
        json.dumps(preflight, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    results: list[RuntimeScenarioResult] = []

    run_id = harness.start_run("Step 4 Scenario A first-pass PASS")
    worker, _ = harness.run_attempt(
        "worker", 1, "complete", phase=LIFECYCLE_SCENARIO_PHASE, ask_before=True
    )
    reviewer, _ = harness.run_attempt("reviewer", 1, "pass", phase=LIFECYCLE_SCENARIO_PHASE)
    results.append(harness.finish(RuntimeScenarioResult("A", run_id, "COMPLETED", 1, [worker, reviewer])))

    run_id = harness.start_run("Step 4 Scenario B FAIL then PASS")
    worker, _ = harness.run_attempt("worker", 1, "complete", phase=LIFECYCLE_SCENARIO_PHASE)
    reviewer1, reviewer_terminal = harness.run_attempt(
        "reviewer",
        1,
        "fail,pass",
        phase=LIFECYCLE_SCENARIO_PHASE,
        findings=("R1",),
        lifecycle="reuse",
        max_dispatches=2,
    )
    correction, _ = harness.run_attempt(
        "worker",
        2,
        "correction",
        phase=LIFECYCLE_SCENARIO_PHASE,
        resolutions={"R1": "RESOLVED"},
    )
    reviewer2, _ = harness.run_attempt(
        "reviewer", 2, "pass", phase=LIFECYCLE_SCENARIO_PHASE, terminal=reviewer_terminal
    )
    results.append(harness.finish(RuntimeScenarioResult("B", run_id, "COMPLETED", 2, [worker, reviewer1, correction, reviewer2])))

    run_id = harness.start_run("Step 4 Scenario C max iterations")
    attempts = []
    for iteration in range(1, 4):
        worker, _ = harness.run_attempt(
            "worker", iteration, "complete" if iteration == 1 else "correction",
            phase=LIFECYCLE_SCENARIO_PHASE,
            resolutions={} if iteration == 1 else {"R1": "DISPUTED"},
        )
        reviewer, _ = harness.run_attempt(
            "reviewer",
            iteration,
            "fail",
            phase=LIFECYCLE_SCENARIO_PHASE,
            findings=("R1",),
        )
        attempts.extend((worker, reviewer))
    results.append(harness.finish(RuntimeScenarioResult("C", run_id, "ESCALATED", 3, attempts)))

    run_id = harness.start_run("Step 4 Scenario D Worker BLOCKED")
    worker, _ = harness.run_attempt("worker", 1, "blocked", phase=LIFECYCLE_SCENARIO_PHASE)
    results.append(harness.finish(RuntimeScenarioResult("D", run_id, "BLOCKED", 1, [worker])))

    run_id = harness.start_run("Step 4 Scenario E Worker unexpected exit")
    worker = harness.observe_unexpected_exit("worker", 1, phase=LIFECYCLE_SCENARIO_PHASE)
    results.append(harness.finish(RuntimeScenarioResult("E", run_id, "ERROR", 1, [worker], recovery=[worker.lifecycle_action])))

    run_id = harness.start_run("Step 4 Scenario F Reviewer unexpected exit")
    worker, _ = harness.run_attempt("worker", 1, "complete", phase=LIFECYCLE_SCENARIO_PHASE)
    reviewer = harness.observe_unexpected_exit("reviewer", 1, phase=LIFECYCLE_SCENARIO_PHASE)
    results.append(harness.finish(RuntimeScenarioResult("F", run_id, "ERROR", 1, [worker, reviewer], recovery=[reviewer.lifecycle_action])))

    results.append(_scenario_g(harness))
    results.append(_scenario_h(harness))
    results.append(_scenario_i(harness))

    return results


def run_final_review_runtime_scenario(artifact_dir: Path) -> RuntimeScenarioResult:
    """Opt-in scenario J: Final Adversarial Review terminal freshness.

    Deliberately NOT part of run_runtime_scenarios(): that function's A-I result set
    is pinned by an exact-set assertion in test_orca_runtime.py, which this change
    may not edit. Scenario J is the exact negative image of scenario B, whose
    reviewer runs with lifecycle="reuse" on a recycled terminal.
    """
    harness = OrcaRuntimeHarness(artifact_dir)
    preflight = harness.preflight()
    (artifact_dir / "environment-final-review.json").write_text(
        json.dumps(preflight, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    run_id = harness.start_run("Final Adversarial Review scenario J terminal freshness")
    worker, _ = harness.run_attempt("worker", 1, "complete", phase=LIFECYCLE_SCENARIO_PHASE)
    phase_reviewer, phase_reviewer_terminal = harness.run_attempt(
        "reviewer", 1, "pass", phase=LIFECYCLE_SCENARIO_PHASE
    )
    # attempt 1: a brand-new terminal. terminal= is NOT passed - that is the scenario.
    # The phase is the gate itself, not the phase under review: a Final Adversarial
    # Review reads the whole run, and its boundary says so.
    final_1, final_terminal_1 = harness.run_attempt(
        "reviewer", 1, "fail", phase=FINAL_REVIEW_PHASE, findings=("R1",)
    )
    correction, _ = harness.run_attempt(
        "worker",
        2,
        "correction",
        phase=LIFECYCLE_SCENARIO_PHASE,
        resolutions={"R1": "RESOLVED"},
    )
    # attempt 2: another brand-new terminal, again with no terminal= argument.
    final_2, final_terminal_2 = harness.run_attempt(
        "reviewer", 2, "pass", phase=FINAL_REVIEW_PHASE
    )

    result = RuntimeScenarioResult(
        "J", run_id, "COMPLETED", 2,
        [worker, phase_reviewer, final_1, correction, final_2],
    )
    result.final_review_terminals = [final_terminal_1, final_terminal_2]
    result.phase_reviewer_terminals = [phase_reviewer_terminal]
    return harness.finish(result)


def run_session_reuse_runtime_scenario(artifact_dir: Path) -> RuntimeScenarioResult:
    """Opt-in scenario K: one worker session and one reviewer session, five phases.

    Deliberately NOT part of run_runtime_scenarios(): that function's A-I result set
    is pinned by an exact-set assertion in test_orca_runtime.py, which this change may
    not edit. Scenario J set the precedent; K follows it.

    The whole chain runs inside ONE scenario because finish() clears the terminal
    ledger: a chain that spanned two scenarios would lose the owner_dispatch_ids the
    reuse aggregates are derived from.

    What `terminal=` each attempt gets is NOT decided by loop position: every attempt
    after the first of a role asks terminal_for_next_dispatch(), which takes a fresh
    observation of the previous dispatch and runs the eight-condition gate. All eight
    hold throughout this scenario -- same role, same agent command, live process,
    settled and finalized predecessor, ownership transferable, not retained, not the
    coordinator's own terminal, not in recovery -- so the run still creates exactly
    two phase terminals for ten dispatches. A gate that refused would hand back None
    and the attempt would open a fresh terminal instead, which is the failure this
    wiring makes observable. Every attempt but the last of each role settles with
    lifecycle="reuse", which issues zero lifecycle commands (W-15 / W-16) and leaves
    ownership to transfer on the next worker-start.
    """
    harness = OrcaRuntimeHarness(artifact_dir)
    preflight = harness.preflight()
    (artifact_dir / "environment-session-reuse.json").write_text(
        json.dumps(preflight, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    phases = CANONICAL_PHASES
    run_id = harness.start_run(SESSION_REUSE_OBJECTIVE)
    attempts: list[RuntimeAttempt] = []
    worker_previous: RuntimeAttempt | None = None
    reviewer_previous: RuntimeAttempt | None = None

    def next_terminal(previous: RuntimeAttempt | None, role: str) -> str | None:
        """Ask the production gate which terminal the next attempt runs on.

        `role` is the intended role of the attempt about to be dispatched, spelled
        exactly as create_fake_terminal spells it when it registers the row, so a
        role swap really is a mismatch rather than a value copied out of the row it
        is being compared against. The agent command is the one the ledger recorded
        for the running session: the fake agent was started with --max-dispatches
        len(phases) and serves every phase of the chain, so the command a reused
        terminal needs IS the command it is already running. No new constant.
        """
        if previous is None:
            return None
        return harness.terminal_for_next_dispatch(
            previous.terminal,
            role="phase_reviewer" if role.endswith("reviewer") else "phase_worker",
            agent_command=harness.ledger_terminal(previous.terminal)["agent_command"],
            dispatch_id=previous.dispatch_id,
        )

    # Real workflow evidence, accumulated as the chain runs: an artifact joins the
    # baseline only after the Reviewer of its phase actually settled with a PASS, so
    # phase N's Reviewer is handed the N-1 artifacts that were genuinely approved and
    # never a placeholder standing in for them.
    approved_baseline: list[str] = []

    for iteration, phase in enumerate(phases, start=1):
        last = iteration == len(phases)
        # Two axes, kept apart. The mode is the fake agent's script
        # ("complete"/"pass") and controls how the process behaves; `phase` is the
        # workflow stage the loop is already carrying, and it is the ONLY value that
        # becomes current_phase (PR #12 MAJOR-1). A terminal is only created when the
        # gate refuses the previous one, and the agent must be given a script it
        # actually knows -- which is why the two cannot be collapsed into one value.
        # One rendered spec per attempt, handed to task-create AND to the dispatch:
        # on the supervised path the Task spec is what Orca replays into the agent's
        # preamble, so a boundary that is not in it never reaches the agent at all.
        # The worker's own artifact contract comes from the boundary, not from
        # evidence: only a Reviewer gets a delta-first context, so passing evidence
        # here would be an argument nothing reads.
        worker_artifact = phase_artifact_contract(
            role="worker", phase=phase, run_id=run_id
        )
        worker_spec, _, _ = dispatch_context(
            "worker",
            iteration,
            "complete",
            phase=phase,
            base_spec=f"worker iteration {iteration}: {phase}",
            run_id=run_id,
        )
        worker, _ = harness.run_existing_task(
            "worker",
            iteration,
            "complete",
            harness.create_task(worker_spec),
            phase=phase,
            spec=worker_spec,
            lifecycle="release" if last else "reuse",
            terminal=next_terminal(worker_previous, "worker"),
            max_dispatches=len(phases),
        )
        # Built AFTER the worker settled and BEFORE the Reviewer is dispatched, which
        # is the only window in which the Reviewer's delta can be a fact rather than
        # a forecast: what the worker claimed and what the runtime recorded for it.
        reviewer_evidence = WorkflowEvidence(
            original_objective=SESSION_REUSE_OBJECTIVE,
            approved_baseline=tuple(approved_baseline),
            current_delta=(worker_artifact,),
            new_claims=(f"{worker_artifact} produced in iteration {iteration}",),
            validation=(
                f"worker outcome={worker.outcome}",
                f"worker task_status={worker.task_status}",
                f"worker dispatch_status={worker.dispatch_status}",
            ),
        )
        reviewer_spec, _, _ = dispatch_context(
            "reviewer",
            iteration,
            "pass",
            phase=phase,
            base_spec=f"reviewer iteration {iteration}: {phase}",
            evidence=reviewer_evidence,
            run_id=run_id,
        )
        reviewer, _ = harness.run_existing_task(
            "reviewer",
            iteration,
            "pass",
            harness.create_task(reviewer_spec),
            phase=phase,
            spec=reviewer_spec,
            evidence=reviewer_evidence,
            lifecycle="release" if last else "reuse",
            terminal=next_terminal(reviewer_previous, "reviewer"),
            max_dispatches=len(phases),
        )
        if reviewer.outcome == "succeeded":
            approved_baseline.append(worker_artifact)
        worker_previous, reviewer_previous = worker, reviewer
        attempts.extend((worker, reviewer))

    result = RuntimeScenarioResult("K", run_id, "COMPLETED", len(phases), attempts)
    result.phase_reviewer_terminals = [
        reviewer_previous.terminal if reviewer_previous else ""
    ]
    return harness.finish(result)


def _scenario_g(harness: OrcaRuntimeHarness) -> RuntimeScenarioResult:
    """Graph-first dependency promotion: no manual readiness override anywhere."""
    run_id = harness.start_run("Step 4 Scenario G graph-first dependency promotion")
    worker_spec = dispatch_context(
        "worker", 1, "complete", phase=LIFECYCLE_SCENARIO_PHASE, run_id=run_id
    )[0]
    worker_task = harness.create_task(worker_spec)
    reviewer_task = harness.create_task(
        dispatch_context(
            "reviewer", 1, "pass", phase=LIFECYCLE_SCENARIO_PHASE, run_id=run_id
        )[0],
        deps=(worker_task,),
    )
    pending_status = harness.task_status(reviewer_task)
    if pending_status != "pending":
        raise OrcaRuntimeError(
            f"reviewer task with an open dependency should be pending, got {pending_status}"
        )
    worker_attempt, _ = harness.run_existing_task(
        "worker", 1, "complete", worker_task, phase=LIFECYCLE_SCENARIO_PHASE, spec=worker_spec
    )
    promoted_status = harness.task_status(reviewer_task)
    if promoted_status != "ready":
        raise OrcaRuntimeError(
            "dependency completion did not promote the reviewer task to ready "
            f"(status={promoted_status}); do not repair this with a manual override"
        )
    reviewer_attempt, _ = harness.run_existing_task(
        "reviewer", 1, "pass", reviewer_task, phase=LIFECYCLE_SCENARIO_PHASE
    )
    if reviewer_attempt.task_id != reviewer_task:
        raise OrcaRuntimeError(
            "scenario G dispatched a different task than the promoted reviewer task"
        )
    result = RuntimeScenarioResult(
        "G", run_id, "COMPLETED", 1, [worker_attempt, reviewer_attempt]
    )
    result.reviewer_task_id = reviewer_task
    result.reviewer_task_status = promoted_status
    return harness.finish(result)


def _scenario_h(harness: OrcaRuntimeHarness) -> RuntimeScenarioResult:
    """Negative control: a dependent created after settlement stays pending forever."""
    run_id = harness.start_run("Step 4 Scenario H late dependent stays pending")
    worker_spec = dispatch_context(
        "worker", 1, "complete", phase=LIFECYCLE_SCENARIO_PHASE, run_id=run_id
    )[0]
    worker_task = harness.create_task(worker_spec)
    worker_attempt, _ = harness.run_existing_task(
        "worker", 1, "complete", worker_task, phase=LIFECYCLE_SCENARIO_PHASE, spec=worker_spec
    )
    late_task = harness.create_task(
        "reviewer iteration 1: pass (created too late)", deps=(worker_task,)
    )
    result = RuntimeScenarioResult("H", run_id, "COMPLETED", 1, [worker_attempt])
    # Observation only: the late dependent is never dispatched.
    result.late_dependent_status = harness.task_status(late_task)
    return harness.finish(result)


def _scenario_i(harness: OrcaRuntimeHarness) -> RuntimeScenarioResult:
    """Never-close regression: self-created is not the same as closable."""
    run_id = harness.start_run("Step 4 Scenario I never-close terminal roles")
    worker_attempt, _ = harness.run_attempt(
        "worker", 1, "complete", phase=LIFECYCLE_SCENARIO_PHASE
    )
    result = RuntimeScenarioResult("I", run_id, "COMPLETED", 1, [worker_attempt])
    # I-2: a simulated coordinator session row is classified without touching runtime.
    result.ledger = [
        harness.classify_terminal(
            handle="term_simulated",
            role="coordinator_session",
            origin="self_created",
            owned_by_this_dispatch=True,
        )
    ]
    # I-3: the self-handle guard must refuse rather than close.
    self_handle = os.environ.get(SELF_HANDLE_ENV)
    if self_handle:
        try:
            harness._teardown_fixture_terminal(handle=self_handle)
        except OrcaRuntimeError:
            result.fixture_teardown = {"selfHandleProbe": "refused"}
        else:
            raise OrcaRuntimeError("self-handle guard did not fire")
    else:
        result.fixture_teardown = {"selfHandleProbe": "unset"}
    return harness.finish(result)
