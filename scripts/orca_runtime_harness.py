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
    from scripts.quality_profile import (
        INVALID_PROFILE_REASON,
        QualityProfileResolution,
        resolve_quality_profile,
    )
    from scripts.task_context import (
        build_agent_routing_context,
        CANONICAL_PHASES,
        FINAL_REVIEW_PHASE,
        TaskContextError,
        build_quality_gate_context,
        build_reviewer_context,
        build_risk_context,
        build_task_boundary,
        ensure_run_artifact_root,
        parse_quality_gate,
        phase_artifact_contract,
        render_task_spec,
        require_workflow_phase,
        strip_task_context,
    )
    from scripts import clarification_protocol, decision_gate, decision_policy, run_logging
    from scripts.workflow_contract import load_workflow_output_contract
except ModuleNotFoundError:  # direct `python3 scripts/...` execution
    from quality_profile import (
        INVALID_PROFILE_REASON,
        QualityProfileResolution,
        resolve_quality_profile,
    )
    from task_context import (
        build_agent_routing_context,
        CANONICAL_PHASES,
        FINAL_REVIEW_PHASE,
        TaskContextError,
        build_quality_gate_context,
        build_reviewer_context,
        build_risk_context,
        build_task_boundary,
        ensure_run_artifact_root,
        parse_quality_gate,
        phase_artifact_contract,
        render_task_spec,
        require_workflow_phase,
        strip_task_context,
    )
    import decision_gate
    import decision_policy
    import run_logging
    import clarification_protocol
    from workflow_contract import load_workflow_output_contract


REPO_ROOT = Path(__file__).resolve().parents[1]
# Resolved once, at import, and never again. dispatch_context used to call
# resolve_quality_profile() whenever its argument was omitted, which meant a profile
# edited while a Worker was running could hand that Worker's Reviewer a DIFFERENT
# quality model than the Worker was given -- the divergence ORIGINAL_REQUEST section
# 10 forbids. Run paths never reach this constant: OrcaRuntimeHarness resolves once
# per run in start_run() and threads its own resolution through every spec. This is
# only the answer for a standalone call with no run behind it, and it is a constant
# precisely so that even that path cannot re-read the file mid-sequence.
REPO_QUALITY_PROFILE = resolve_quality_profile(REPO_ROOT)
# OS-41. Deliberately NOT named after any agent Orca recognizes. On Orca 1.4.196 a
# terminal Orca believes is running a real agent CLI is driven through worker-start's
# supervised prompt-acknowledgement stage, which only a genuine agent session can
# complete; a shim borrowing the name `codex` was therefore pushed into an
# unrecoverable `agent_prompt_stalled` that also marks the Task failed. Under an
# honest name the runtime refuses up front with `agent_unconfigured`, creating no
# Dispatch and leaving the Task `ready`, and the run takes the version-matched
# guide's documented tracked-Dispatch path. See docs/COMPATIBILITY.md.
FAKE_AGENT_SHIM = REPO_ROOT / "scripts" / "fake_bin" / "fake-agent"
# OS-17 review: the same field/value vocabulary orca_fake_agent.py already reads
# out of SKILL.md to build a fake reviewer's own response, read here once so
# _reviewer_gate_result()/_reviewer_review_verdict() below can recognize that
# response in a settled attempt's body without hardcoding a private mode vocabulary
# that belongs to the fake agent, not to this harness.
REVIEWER_VERDICT_CONTRACT = load_workflow_output_contract(
    REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
)
WAIT_TYPES = "worker_done,escalation,question"
# ---- OS-41: worker-start launch outcome vocabulary ------------------------------
# Orca 1.4.196 answers a `worker-start` that did NOT produce a running worker with
# `ok: true` and a structured launch result whose `state` carries the real outcome
# ("The call exits 0 only for ready" -- `orca agent-context --json`). `dispatchId` is
# present on a FAILED start too, so reading it alone is exactly the "prompt delivery
# inferred from Task/Dispatch existence" this repository must not do: an
# `agent_prompt_stalled` start would be recorded as a supervised attachment for a
# Dispatch the runtime had already given up on.
WORKER_START_READY_STATE = "ready"
# The ONE point observation whose `worker-start` success receipt carries no `state`
# key at all. Orca 1.4.184 predates the field, so on that runtime -- and only there --
# an absent `state` is the shape of a successful start rather than missing lifecycle
# evidence. It is pinned to the exact version string, not to "any runtime older than
# 1.4.196" and not to "any listed version": a stateless receipt from 1.4.196, from a
# future point observation, or from a runtime this harness never identified is a
# MALFORMED success receipt, and OS-41 requires that to fail closed. `preflight()`
# is what supplies the identity, and it stores it only after validate_orca_contract()
# has accepted the runtime, so the exception can never be unlocked by an unverified
# version string. (BUGFIX-I1-MAJOR-1.)
#
# PR #29 review MAJOR-2 CONSEQUENCE: 1.4.184 is no longer in
# SUPPORTED_ORCA_APP_VERSIONS, so preflight() can no longer produce this identity and
# this allowance is UNREACHABLE FROM A LIVE RUN of the current head. It is kept, not
# deleted, for two reasons: keeping it changes nothing about what the live gate
# accepts (validate_orca_contract() already refuses 1.4.184 before start_worker() is
# ever reached, so the effective behaviour is strictly fail-closed), and it preserves
# the reading that was actually derived from 1.4.184 receipts so a future revision
# that re-verifies that runtime does not have to re-derive it. Its coverage is
# therefore OFFLINE ONLY -- the contract tests set the identity directly.
WORKER_START_STATELESS_RECEIPT_VERSION = "1.4.184"

# ---- OS-41: the runtime's own unexpected-exit report ---------------------------
# Orca 1.4.196 publishes an `escalation` of its own when a dispatched agent process
# ends without settling ("Agent exited unexpectedly (Agent process ended; this host
# cannot report why)"), carrying taskId, dispatchId, exitCode, exitCause and handle
# in its payload. Orca 1.4.184 published nothing, which is why observe_unexpected_exit
# used to treat ANY message in that checkpoint as a contract violation. The report is
# evidence, not a violation -- but only when it is bound to the very Dispatch, Task and
# terminal being observed AND was written by the runtime rather than by the agent, which
# is what _runtime_exit_report_defects() below proves. Anything else in the delivery, a
# `worker_done` above all, still fails the scenario: the whole claim under test is that
# this dispatch produced no lifecycle result.
RUNTIME_EXIT_REPORT_TYPE = "escalation"
# BUGFIX-I1-MAJOR-2. `type` plus the two identity fields is NOT a discriminator: an
# agent's own escalation is the same type and, when it names the same round, carries
# the same two ids. Both receipts were captured from the live 1.4.196 runtime in one
# session so the difference is observed rather than imagined:
#
#   runtime exit report   subject "Agent exited unexpectedly (Agent process ended;
#                                  this host cannot report why)"
#                         priority "high"
#                         payload {"taskId","dispatchId","exitCode":0,
#                                  "exitCause":{"kind":"unknown",
#                                               "reason":"host_status_unavailable"},
#                                  "handle":"term_..."}
#
#   fake agent escalation subject "Blocked: deterministic fake"
#                         priority "normal"
#                         payload {"taskId","dispatchId"}          <- ID-ONLY
#
# Those two receipts differ in shape, and the shape difference is worth checking --
# but it is NOT authorship evidence, and this block must not be read as claiming it is.
# `orca orchestration send` accepts arbitrary `--payload`, plus `--subject` and
# `--priority` (verified on the live 1.4.196 CLI: `orca orchestration send --help`
# and `orca agent-context --json`). A dispatched agent can therefore reproduce the
# runtime's subject, its `high` priority, and every payload field below --
# `exitCode`, `exitCause` and `handle` included. Each of these clauses is FORGEABLE.
#
# They are kept as DEFENCE IN DEPTH and as IDENTITY/SHAPE VALIDATION only: they pin
# the message to this Task, this Dispatch and this terminal, and they reject a
# malformed report. The single observed AUTHORSHIP discriminator is the top-level
# stored `sender_pane_key` field, immediately below.
RUNTIME_EXIT_REPORT_SUBJECT_PREFIX = "Agent exited unexpectedly"
RUNTIME_EXIT_REPORT_PRIORITY = "high"
# ---- FINAL-I1-MAJOR-1: sender provenance is the load-bearing discriminator --------
# The clauses above are all FORGEABLE. `orca orchestration send` on the live 1.4.196
# CLI accepts --subject, --priority AND --payload (verified against
# `orca orchestration send --help` and `orca agent-context --json`), so a dispatched
# agent can reproduce the runtime's subject, its `high` priority and every payload
# field -- exitCode, exitCause and handle included. An earlier revision of this file
# claimed otherwise; that claim was wrong, and shape alone therefore proves nothing
# about WHO wrote the message.
#
# `sender_pane_key` is the field the runtime controls. Note WHERE it lives: it is a
# TOP-LEVEL STORED FIELD ON THE MESSAGE RECORD, written by the runtime when it stores
# the message -- NOT a member of the JSON `payload`, and not settable by any
# `orchestration send` flag. That is exactly why it is the one clause an agent cannot
# reproduce: `--payload` reaches the payload object only.
#
# On the captured 1.4.196 receipts it is PRESENT-and-null on the runtime's own report
# and PRESENT-and-non-null on an agent's message, and a dispatched agent could not
# null it in any of four attempts made from its own dispatched terminal:
#
#   1. `orca orchestration send ...`                        -> pane key set
#   2. `env -u ORCA_PANE_KEY orca orchestration send ...`    -> pane key set
#   3. `setsid orca orchestration send ...`                  -> pane key set
#   4. `nohup ... & disown`, and a no-tty subshell           -> pane key set
#
# So the runtime resolves the sending pane from process ancestry, not from the
# environment the agent can edit. Requiring `None` is therefore a real authorship
# check and not a cosmetic one.
#
# ABSENT IS REJECTED, NOT TREATED AS NULL. That is the whole correction: an omitted
# key is indistinguishable from a null one, and under this run's fail-closed policy
# "indistinguishable" is a reason to REJECT, never a reason to accept. The runtime's
# real receipt always carries the key, so requiring its presence costs a genuine
# report nothing.
#
# Scope of the claim, stated rather than implied: this is proven for a dispatched
# agent sending through `orchestration send`, which is the channel the threat model
# covers. It is not a claim that no process anywhere can ever produce a null pane key.
#
# False-negative behaviour, also explicit. If a genuine runtime report ever arrives
# without the key, or carrying a non-null value, this predicate REFUSES it and the
# observation fails loudly -- it is not silently downgraded to "accept anyway". A
# false negative here is a visible test failure with the defect named; a false
# positive would be a worker-authored message admitted as runtime evidence. Failing in
# the first direction is the whole point, and if the runtime's schema ever changes the
# correct response is to re-capture a receipt and re-derive the clause, never to
# weaken it.
RUNTIME_EXIT_REPORT_SENDER_PANE_KEY = None
# What a dependent Task created AFTER its dependency already settled reports at
# creation. Orca 1.4.184 left it `pending`; Orca 1.4.196 evaluates the dependency
# immediately and reports `ready`. Verified directly against 1.4.196 rather than
# inferred: a dependent whose dependency is still OPEN is `pending` there too, so
# `ready` is a satisfied-dependency answer and not a lost dependency edge. Neither
# value is a defect, and scenario H does not turn on which one appears -- what it
# actually protects is that the coordinator never DISPATCHES such a Task, which is
# asserted separately from this status.
LATE_DEPENDENT_STATUSES = frozenset({"pending", "ready"})
# OS-41. POINT VERIFICATIONS OF *THIS* REVISION, not a range and not a history.
#
# This tuple is an EXECUTABLE CLAIM: every entry is an Orca app version that the
# CURRENT head of this repository has actually run the Step 4 real-runtime suite
# against, end to end, and observed to pass. It is therefore not the list of every
# version this repository has ever been observed on -- an observation made against an
# OLDER harness revision does not carry forward across changes to that harness, and
# re-listing it here would advertise support this revision has not demonstrated.
#
# Membership is exact-string set containment, never an ordering comparison, so an
# unverified 1.4.190 or 1.4.197 still fails closed even though it sits between / after
# observed points. Adding an entry REQUIRES a fresh runtime run OF THE REVISION THAT
# ADDS IT, and the guide-grammar check below runs for every entry -- a version whose
# live guides drifted is rejected on the grammar branch even while it is listed.
#
# PR #29 review MAJOR-2: 1.4.184 was listed here while the only real-runtime evidence
# for this head was a 1.4.196 run. This revision changed worker-start admission, the
# fake-agent shim name and path, unexpected-exit classification, scenario K, packaging
# and the run-scoped decision cursor; the 1.4.184 artifacts predate all of that. The
# 1.4.184 and 1.4.178-rc.2 records are PRESERVED -- unmodified on disk, and named in
# HISTORICAL_ORCA_APP_VERSION_OBSERVATIONS below and in docs/COMPATIBILITY.md -- but
# they are historical observations of older revisions, not current executable support.
SUPPORTED_ORCA_APP_VERSIONS: tuple[str, ...] = ("1.4.196",)
# The newest point verification. Kept as a single string because callers and tests
# that only need "a version this harness accepts" read it; the gate itself reads the
# tuple above and never this.
SUPPORTED_ORCA_APP_VERSION = SUPPORTED_ORCA_APP_VERSIONS[-1]
# HISTORICAL POINT OBSERVATIONS. Runtimes on which an OLDER revision of this
# repository was observed to pass, recorded so the evidence is not lost and so the
# distinction is machine-checkable rather than only prose. Deliberately NOT consulted
# by validate_orca_contract(): this tuple grants nothing and gates nothing. A runtime
# reporting one of these versions is refused exactly like any other unverified version
# until THIS revision is actually run against it and the entry is moved above.
#
#   1.4.184     -- deterministic real-Orca integration with fake agents, on the
#                  pre-OS-41 harness. That is the revision on which the SUPERVISED
#                  fake-agent adoption path (and therefore granted session reuse) was
#                  observed; see docs/validation/historical/ and docs/COMPATIBILITY.md.
#   1.4.178-rc.2 -- real claude-glm / claude-gemma smoke test in the company fixture.
HISTORICAL_ORCA_APP_VERSION_OBSERVATIONS: tuple[str, ...] = ("1.4.178-rc.2", "1.4.184")
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


class DecisionGateRefused(OrcaRuntimeError):
    """OS-29 B1: a boundary refused BEFORE any Task or Dispatch was created.

    Raised rather than returned, which is the shape this class already gives every
    other pre-dispatch failure (an invalid quality profile, an undeclared
    requested_phases at the final gate): the refusal is logged through
    _log_pre_dispatch_failure and then re-raised unchanged, so no caller can mistake
    it for a settled attempt and no Dispatch id ever comes into existence.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}{(' -- ' + detail) if detail else ''}")
        self.reason = reason
        self.detail = detail


class UnsupportedOrcaContract(OrcaRuntimeError):
    pass


def validate_orca_contract(
    app_version: str, orchestration_guide: str, cli_guide: str
) -> None:
    # Fail closed on an absent or non-string version BEFORE the membership test:
    # `None`/"" would otherwise reach the message below and read as a mere version
    # mismatch, when what actually happened is that the runtime told us nothing about
    # its identity. Unknown identity is not a version we can point-verify.
    if not isinstance(app_version, str) or not app_version.strip():
        raise UnsupportedOrcaContract(
            "installed runtime did not report an app version; refusing to run the "
            f"real-runtime suite against an unidentified Orca (got {app_version!r})"
        )
    if app_version not in SUPPORTED_ORCA_APP_VERSIONS:
        raise UnsupportedOrcaContract(
            "runtime harness point-verifies Orca "
            f"{', '.join(SUPPORTED_ORCA_APP_VERSIONS)}; "
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
    # The profile-first block, parsed back out of the spec this attempt dispatched.
    # The two fields above carry layer-1 values and Reviewer key NAMES, so neither
    # can answer "which quality attributes did this dispatch actually carry" -- the
    # question a phase-filtering assertion is made of.
    quality_gate: tuple[tuple[str, str], ...] = ()
    # OS-4: the routing block this attempt was dispatched with, parsed back out of
    # its own spec. () for a legacy attempt, which renders no such block.
    agent_routing: tuple[tuple[str, str], ...] = ()


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
    # ---- scenario L: what the run's one profile resolution was, and what each
    # dispatch was told applied to it.
    quality_profile_status: str = ""
    quality_profile_attributes: dict[str, str] = field(default_factory=dict)
    # ---- reuse aggregates (W-18), filled by finish() before the ledger is cleared
    reuse_chains: dict[str, list[str]] = field(default_factory=dict)
    terminal_creations: int = 0
    # ---- OS-41: what the production reuse gate actually ANSWERED at each
    # same-role transition, refusal reasons included. Scenario K used to prove
    # only the granted path; on a runtime where the gate's supervised evidence
    # does not exist the refusal is the result under test, and a refusal that
    # is not recorded is indistinguishable from a gate nobody asked.
    reuse_decisions: list[dict[str, Any]] = field(default_factory=list)
    retained_terminals: list[str] = field(default_factory=list)
    # ---- PR #29 review MAJOR-1: the point-verified Orca app version this result was
    # produced on, filled by finish() from the identity preflight() recorded on the far
    # side of validate_orca_contract(). "" means no runtime identity was ever proven.
    orca_app_version: str = ""
    # ---- OS-3: what strength this run enforced, and what the graph looked like.
    risk: str = ""
    risk_source: str = ""
    phase_reviewer_task_ids: list[str] = field(default_factory=list)
    reviewer_gates_skipped: list[str] = field(default_factory=list)


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


@dataclass(frozen=True)
class _PendingVerification:
    """The one admitted B3-V verification, remembered between B1 and B3.

    OS-29 P6b row 2 lets a blocking Worker classification be verified by the
    ALREADY-SCHEDULED current-phase Reviewer and by nothing else. B1 decides that
    (decision_gate.admit_head with a VerificationDispatch); this carries the two
    things the B3 side then needs and cannot re-derive safely: WHICH Worker record
    the Reviewer owes a bound `verifies` reference to, and that Worker's own
    classification, which decision_gate.evaluate_verification compares against.

    `worker` is read off the ADMITTED LEDGER HEAD, not off the Reviewer's body, so a
    Reviewer cannot restate the classification it is supposed to be verifying.
    """

    worker_key: str
    worker: "decision_gate.GateResult"
    round: tuple[str, str, int]


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
    quality_profile: QualityProfileResolution | None = None,
    requested_phases: tuple[str, ...] = (),
    risk: str = "high",
    risk_source: str = "default",
    agent_routing: Any | None = None,
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

    `quality_profile` is the project's resolved Quality Profile, and it reaches BOTH
    roles through the same block. A Worker that is not told which quality attributes
    block its phase produces correction rounds for rules it never received, and a
    Reviewer told something different from the Worker is judging against a spec that
    was never dispatched -- so the two payloads are built from one resolution, not
    two. It defaults to reading the repository this harness runs against, which is
    also the tree `drill_down` points at.

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
    # Never resolved here. A caller inside a run passes the run's own resolution; a
    # caller outside one gets the import-time constant. Neither branch reads the file
    # again, so two specs built moments apart cannot describe two different profiles.
    if quality_profile is None:
        quality_profile = REPO_QUALITY_PROFILE
    # External review MAJOR: an undeclared requested set at the final gate used to
    # resolve to every applicable phase, which can hand the Final Adversarial Review
    # an attribute scoped to a phase this run never requested (DESIGN, BUGFIX,
    # REFACTORING-only rules reaching an implementation+test run) and manufacture a
    # false blocking violation / correction loop. requested_phases is passed straight
    # through instead: build_quality_gate_context already fails closed (raises) when
    # the final_review gate has no requested set, and that fail-closed behaviour is
    # the whole fix -- broadening was never a real fallback, it was the defect.
    quality_gate = build_quality_gate_context(
        resolution=quality_profile,
        current_phase=phase,
        requested_phases=requested_phases,
    )
    # OS-3: a separate block from the quality gate, built by a builder that takes no
    # QualityProfileResolution -- the two axes share no argument and no key.
    risk_context = build_risk_context(
        risk=risk, risk_source=risk_source, current_phase=phase
    )
    # OS-4: a third block, built only when this run selected a profile. None is the
    # legacy answer, and render_task_spec() then omits the block entirely -- which is
    # why a profile-less dispatch keeps rendering byte-identical text.
    routing_context = (
        None
        if agent_routing is None
        else build_agent_routing_context(routing=agent_routing, current_phase=phase)
    )
    return (
        render_task_spec(
            base,
            boundary,
            reviewer_context,
            quality_gate,
            risk_context,
            routing_context,
        ),
        boundary,
        reviewer_context,
    )


def _reviewer_gate_result(role: str, body: str) -> str:
    """The two-valued workflow gate (PASS/FAIL) already written into a settled
    attempt's body -- the value that actually drives the correction loop.

    OS-17 review MAJOR: `attempt.outcome` only says the dispatch/process settled
    successfully -- a Reviewer settles just as successfully when its gate result is
    FAIL (the normal correction-loop case) as when it is PASS, so `outcome=succeeded`
    alone cannot answer "did this phase/iteration PASS?". This reads the actual
    `RESULT: PASS`/`RESULT: FAIL` line the settled dispatch wrote, using the same
    field/value vocabulary SKILL.md documents (REVIEWER_VERDICT_CONTRACT), rather
    than guessing from the caller's dispatch `mode` -- which would only work for this
    repository's own scripted fake reviewer, not for a real one. A non-reviewer role,
    or a body that never wrote a recognizable line (an unexpected exit, a malformed
    response), both correctly resolve to "" -- an unresolved result is a blank, not a
    guess. See _reviewer_review_verdict() below for the separate, richer, four-valued
    report annotation this two-valued gate cannot preserve on its own.
    """
    if not role.endswith("reviewer"):
        return ""
    field = REVIEWER_VERDICT_CONTRACT.reviewer_field
    pass_line = f"{field}: {REVIEWER_VERDICT_CONTRACT.reviewer_pass}"
    fail_line = f"{field}: {REVIEWER_VERDICT_CONTRACT.reviewer_fail}"
    for line in body.splitlines():
        stripped = line.strip()
        if stripped == pass_line:
            return REVIEWER_VERDICT_CONTRACT.reviewer_pass
        if stripped == fail_line:
            return REVIEWER_VERDICT_CONTRACT.reviewer_fail
    return ""


def _reviewer_review_verdict(role: str, body: str) -> str:
    """OS-1's separate four-valued report annotation (PASS / PASS WITH NOTES / FAIL /
    BLOCKED), already written into a settled attempt's body as `REVIEW_VERDICT: ...`.

    OS-17 review round 3 MAJOR-2: the two-valued workflow gate `_reviewer_gate_result`
    reads collapses PASS WITH NOTES into PASS and BLOCKED into FAIL (reviews/common.md
    §Verdict's own mapping) -- exactly the review-level distinction a column named
    for "the verdict" should not silently lose. Parsed the same way as the gate
    result: an exact line match against the vocabulary SKILL.md documents
    (REVIEWER_VERDICT_CONTRACT.review_verdict_values), never inferred from the
    two-valued RESULT line. A non-reviewer role, or a body that never wrote a
    recognizable REVIEW_VERDICT line, both resolve to "" rather than a guess.
    """
    if not role.endswith("reviewer"):
        return ""
    field = REVIEWER_VERDICT_CONTRACT.review_verdict_field
    lines = {line.strip() for line in body.splitlines()}
    for value in REVIEWER_VERDICT_CONTRACT.review_verdict_values:
        if f"{field}: {value}" in lines:
            return value
    return ""


class OrcaRuntimeHarness:
    def __init__(
        self,
        artifact_dir: Path,
        *,
        wait_timeout_ms: int = 10000,
        quality_profile_root: Path = REPO_ROOT,
        risk: str = "high",
        risk_source: str = "default",
        agent_routing: Any | None = None,
        human_approval_port: Any | None = None,
        clarification_inputs: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.orca = self._resolve_orca()
        self.artifact_dir = artifact_dir
        self.human_approval_port = human_approval_port or clarification_protocol.ArtifactHumanApprovalPort(artifact_dir)
        self.clarification_inputs = clarification_inputs or {}
        self.clarification_errors: list[str] = []
        self.wait_timeout_ms = wait_timeout_ms
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.run_owner: str | None = None
        self.run_id: str | None = None
        # The requested workflow phases for the current run, set once by start_run()
        # and never inferred from which attempts happen to occur (a correction round
        # can dispatch a phase without that phase having been "requested"). Empty
        # until start_run() is called, and empty is exactly the state that must fail
        # closed at the final_review gate rather than silently widen to every phase --
        # see the External review MAJOR note in dispatch_context().
        self.requested_phases: tuple[str, ...] = ()
        # OS-4: the run's single materialized routing, or None on the legacy path.
        # Set once by the caller before the first dispatch and never re-resolved per
        # attempt -- that is the property corrections and re-reviews depend on.
        # None is the default so a scenario that selects no profile dispatches the
        # same specs and records the same ledger values as before this field existed.
        self.agent_routing = agent_routing
        # The tree the run's quality profile is read from, and the ONE resolution
        # every spec this harness renders is built from. start_run() re-reads it once
        # at the run boundary and then nothing re-reads it until the next run: a
        # Worker and the Reviewer that judges it must be handed the same quality
        # model even if somebody edits the profile in between.
        self.quality_profile_root = quality_profile_root
        # ---- OS-3: run-scoped strength. Validated in start_run(), then frozen: every
        # spec, graph and log row of the run reads this one pair.
        self.risk = risk
        self.risk_source = risk_source
        self.quality_profile: QualityProfileResolution = resolve_quality_profile(
            quality_profile_root
        )
        self._raw: list[dict[str, Any]] = []
        self._signals: list[str] = []
        # handle -> terminal row (authoritative role/origin, survives across dispatches)
        self._terminals: dict[str, dict[str, Any]] = {}
        # dispatch_id -> lifecycle row (axis outcomes + finalization state)
        self._ledger: dict[str, dict[str, Any]] = {}
        # OS-17: when this run's ORCHESTRATOR_LOG.md/TIMING_LOG.md were first opened
        # (start_run()) and the wall-clock start log_run_status() diffs against.
        # Empty until start_run() runs, same lifecycle as run_id/run_owner.
        self._run_started_at: str = ""
        # OS-17: best-effort logging must never change lifecycle correctness, so a
        # write failure is caught and recorded here rather than raised -- see
        # _log_attempt(). Empty in the overwhelmingly common case; a test can assert
        # against it to catch a real bug in the logging helper itself.
        self._logging_errors: list[str] = []
        # ---- OS-29. The decision policy this harness's B1 guard evaluates against,
        # resolved once from the orchestration Skill this runtime IS -- the same
        # one-resolution rule quality_profile above follows.
        self._decision_policy = decision_policy.load_decision_policy(
            REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        # The (run, phase, iteration) of the round this PROCESS last settled and
        # recorded in the ledger, or None. A3's binding expectation, held in memory
        # and never read back off the ledger it validates. It is deliberately
        # process-local: OS-31 owns cross-session resume, so a fresh Coordinator
        # meeting a non-trivial ledger fails closed rather than guessing (L7).
        self._last_settled: tuple[str, str, int] | None = None
        # OS-41 diagnostic: the reuse gate's most recent answer (see
        # terminal_for_next_dispatch). None until the gate has been asked once.
        self.last_reuse_decision: dict[str, Any] | None = None
        # OS-41 (BUGFIX-I1-MAJOR-1). The app version of the runtime this harness has
        # VALIDATED, written only by preflight() and only after
        # validate_orca_contract() accepted it. "" means "no runtime has been
        # identified", which is the fail-closed default: version-conditional
        # allowances are refused until an identity has actually been proven, so a
        # harness that never ran preflight() cannot inherit another runtime's
        # exceptions.
        self.orca_app_version: str = ""
        # OS-29 B3-V. Armed by _b1_guard() at the ONE B1 that admits a
        # verification Reviewer past an open blocking head, consumed by the very
        # next settled Reviewer attempt of that same round, and cleared by
        # anything else. It is never a permission of its own: the admission is
        # decided by decision_gate.admit_head(), and this only remembers WHICH
        # Worker record the admitted Reviewer owes a bound verification of.
        self._pending_verification: _PendingVerification | None = None
        # OS-17 review round 4 MAJOR: the currently-open phase/iteration TIMING_LOG
        # boundary, if any -- advanced automatically by _open_phase_iteration_
        # boundary(), called just before a dispatch starts (run_existing_task(),
        # observe_unexpected_exit()) so its own started_at brackets that dispatch
        # rather than trailing it, and closed by finish() for whatever is still
        # open when the run ends.
        # round 5 review MAJOR: the tracker's *_last_ended_at fields hold the
        # ended_at of the most recent attempt actually inside the currently open
        # scope (advanced by _log_attempt() on every settled attempt) so that
        # closing an OUTGOING scope on a transition uses that scope's own last
        # real activity, never "whenever the next scope's dispatch happens to
        # settle" -- otherwise an outgoing iteration/phase's duration would
        # silently include the next one's dispatch time.
        # OS-19: that state and its transitions now live in
        # run_logging.RunTimingTracker rather than in eight fields here, because
        # the CLI path (a live Coordinator, one process per event) needs exactly
        # the same lifecycle and had none. One class, two storage strategies --
        # in memory here, a JSON file there -- so the two paths cannot drift into
        # different timing semantics. `emit` routes every row the tracker writes
        # through _safe_log, which is what keeps section 9's "a logging failure
        # never changes lifecycle correctness" true for boundary rows too.
        self._timing: run_logging.RunTimingTracker | None = None

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
        eligible, reasons = self.reuse_eligible(
            handle,
            role=role,
            agent_command=agent_command,
            dispatch_id=dispatch_id,
            observation=self.observe_for_reuse(
                dispatch_id=dispatch_id, handle=handle
            ),
        )
        # OS-41. Diagnostic only, written after the decision and read by nothing that
        # can change it: the gate's own answer for this transition, so a scenario can
        # assert WHICH conditions refused rather than only that a fresh terminal
        # appeared. It records; it does not decide.
        self.last_reuse_decision = {
            "handle": handle,
            "role": role,
            "dispatch_id": dispatch_id,
            "eligible": eligible,
            "reasons": list(reasons),
        }
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
        # OS-41 (BUGFIX-I1-MAJOR-1). Recorded ONLY here and ONLY on the far side of
        # validate_orca_contract(), so every version-conditional allowance downstream
        # is keyed to an identity this harness actually point-verified rather than to
        # whatever string the runtime happened to report.
        self.orca_app_version = status["runtime"]["appVersion"]
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

    def start_run(
        self, objective: str, *, requested_phases: tuple[str, ...] = ()
    ) -> str:
        """STEP 0. `requested_phases` is the run-scoped set every final_review
        dispatch of this run is judged against (external review MAJOR): explicit,
        not inferred from which attempts happen to occur, and validated here so a
        typo'd phase fails at the run boundary rather than inside a rendered spec.
        Left empty for a run that never reaches final_review; a run that does and
        never declared one fails closed at that dispatch instead of silently
        widening to every applicable phase.
        """
        for candidate in requested_phases:
            require_workflow_phase(candidate, field="requested_phases")
        if self.risk not in RISK_LEVELS:
            raise OrcaRuntimeError(
                f"INVALID_RISK: {self.risk!r} is not one of {RISK_LEVELS}; no Run is "
                "created and no Task is dispatched"
            )
        # STEP 0, before the run terminal and long before the first Task. The run's
        # quality model is read exactly once, here, and an invalid profile stops the
        # run at its boundary instead of at the first spec that needs it: nobody can
        # produce a trustworthy verdict for this project, so there is nothing worth
        # dispatching. Everything after this point reads self.quality_profile.
        self.quality_profile = resolve_quality_profile(self.quality_profile_root)
        if self.quality_profile.is_invalid:
            raise OrcaRuntimeError(
                f"{INVALID_PROFILE_REASON}: {self.quality_profile.path} exists but is "
                f"not a valid quality profile ({self.quality_profile.error}); no Run "
                "is created and no Task is dispatched"
            )
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
        self.requested_phases = tuple(requested_phases)
        self._signals = []
        self._ledger = {}
        # OS-41. The OS-29 decision-gate cursor is RUN-SCOPED and must be cleared
        # here, beside the other per-run resets, for the same reason they are: one
        # OrcaRuntimeHarness starts several Runs in sequence (run_runtime_scenarios
        # drives A..I on a single instance), and a new Run opens a brand-new ledger
        # whose only record is its own run-entry declaration. Carrying the previous
        # Run's settled round forward makes admit_head() refuse that legitimate first
        # boundary as DECISION_GATE_INPUT_UNBOUND -- "the run-entry declaration is
        # the head but this is not the run's first boundary" -- which is exactly what
        # the 1.4.196 runtime suite hit on Scenario B's very first dispatch. The
        # armed verification is per-round and belongs to the Run that armed it, so it
        # is cleared with the same statement.
        self._last_settled = None
        self._pending_verification = None
        # Provisioned here, once, immediately after the run id is known -- and
        # BEFORE any caller can create a Task whose artifact_contract names this
        # directory. Scoped under artifact_dir (this harness's own scratch space),
        # never the real repository's artifacts/ root, so exercising this path in
        # tests cannot litter the working tree with run directories.
        # OS-29: the run root and the run-entry decision declaration in ONE
        # statement, at the same point the run root was already provisioned and
        # adjacent to the ORCHESTRATOR_LOG/TIMING_LOG opens below -- so the first
        # pre-dispatch B1 guard has an explicit, validated record to read instead of
        # an absence, and a run root can never exist without a ledger.
        run_logging.open_decision_ledger(
            self.run_id,
            base=self.artifact_dir,
            phases=self.requested_phases,
            risk=self.risk or "",
            ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION,
        )
        # OS-17: ORCHESTRATOR_LOG.md/TIMING_LOG.md open here, in the same
        # already-provisioned root, one line each -- the run's own start
        # timestamp is recorded once and reused by log_run_status() for the
        # wall-clock duration, never re-read from the filesystem.
        self._run_started_at = run_logging.now_iso()
        self._timing = run_logging.RunTimingTracker(
            self.run_id,
            base=self.artifact_dir,
            emit=self._emit_timing_row,
            risk=self.risk,
        )
        self._timing.run_started_at = self._run_started_at
        self._safe_log(
            run_logging.log_orchestrator_event,
            self.run_id,
            base=self.artifact_dir,
            event="run_start",
            risk=self.risk,
            risk_source=self.risk_source,
            requested_phases=",".join(self.requested_phases),
            detail=objective,
            timestamp=self._run_started_at,
        )
        self._safe_log(
            run_logging.log_timing_event,
            self.run_id,
            base=self.artifact_dir,
            event="run_start",
            started_at=self._run_started_at,
            risk=self.risk,
            timestamp=self._run_started_at,
        )
        self.log_agent_routing_evidence()
        return self.run_id

    def log_agent_routing_evidence(self) -> int:
        """Write this run's agent-routing evidence. Returns the number of rows.

        Here rather than before the Run because the log is run-scoped and needs the
        run id; the ROUTING itself was materialized earlier, before any Run existed,
        and is only being reported now.

        Zero rows on the legacy path -- evidence_rows() returns () for a routing with
        no profile, and there is no routing at all when `profile=` was omitted. That
        is what keeps a profile-less run's ORCHESTRATOR_LOG.md byte-identical to one
        produced before OS-4.

        Every entry is written, optional ones included: "not dispatched at this risk
        level" is a statement about the lifecycle, not permission to leave a resolved
        command out of the record.
        """
        if self.agent_routing is None or not self.run_id:
            return 0
        rows = self.agent_routing.evidence_rows()
        for row in rows:
            self._safe_log(
                run_logging.log_orchestrator_event,
                self.run_id,
                base=self.artifact_dir,
                **row,
            )
        return len(rows)

    def create_task(self, spec: str, *, deps: tuple[str, ...] = ()) -> str:
        assert self.run_owner
        args = ["orchestration", "task-create", "--spec", spec]
        if deps:
            args.extend(["--deps", json.dumps(list(deps))])
        args.extend(["--from", self.run_owner])
        created = self.call(*args)
        return created["result"]["task"]["id"]

    def create_phase_graph(
        self, worker_spec: str, reviewer_spec: str | None = None
    ) -> tuple[str, str | None]:
        """SKILL.md section 6 step 2, in one place instead of per scenario.

        MEDIUM/HIGH -> (worker_task, reviewer_task) with the dependency edge declared
                       before the Worker is dispatched.
        LOW         -> (worker_task, None); no dependent Reviewer node is created at
                       all, so nothing is promoted to ready and then abandoned.

        The Final Adversarial Review does NOT go through this method: section 17's
        Task is a single node with no dependencies, created at every risk level,
        LOW included.
        """
        worker_task = self.create_task(worker_spec)
        if self.risk == "low" or reviewer_spec is None:
            return worker_task, None
        return worker_task, self.create_task(reviewer_spec, deps=(worker_task,))

    def log_reviewer_gate_skipped(self, phase: str) -> None:
        """One positive row per phase whose Reviewer gate LOW skips.

        The absence of a reviewer row must never be the only evidence that a gate
        was skipped -- that is indistinguishable from a crash or a dropped write.
        """
        if not self.run_id:
            return
        self._safe_log(
            run_logging.log_orchestrator_event,
            self.run_id,
            base=self.artifact_dir,
            event="reviewer_gate_skipped",
            phase=phase,
            risk=self.risk,
            detail="risk=low: no phase Reviewer gate for this phase",
        )

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
        phase: str = "",
    ) -> str:
        command = [
            "exec",
            str(FAKE_AGENT_SHIM),
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
        # W-20 / OS-4: the value the reuse gate's condition 2 compares. Without a
        # routing this stays the launch command line, exactly as before. With one it
        # becomes the RESOLVED ROLE COMMAND for this phase, which is the identity
        # that actually decides whether the next task may keep this session: a
        # profile can route two phases of the same role to different agents, and
        # reusing a session across that change would hand the next phase the wrong
        # agent while every other reuse condition still passed.
        agent_command = self.resolved_agent_command(role, phase) or shlex.join(command)
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

    def resolved_agent_command(self, role: str, phase: str = "") -> str:
        """The resolved command for `role` in `phase`, or "" when there is no routing.

        Reads the run's already-materialized routing; resolves nothing. "" is the
        legacy answer, and every caller falls back to what it used before -- which is
        what keeps a scenario that selected no profile dispatching the same commands
        and recording the same ledger values as before this method existed.
        """
        if self.agent_routing is None:
            return ""
        if role.startswith("final"):
            entry = self.agent_routing.for_role("final_review", "final_reviewer")
        else:
            entry = self.agent_routing.for_role(
                phase, "reviewer" if role.endswith("reviewer") else "worker"
            )
        return entry.command if entry is not None else ""

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
            # OS-41 STEP 3a. The acknowledgement gate, ABOVE the ledger write: a
            # supervised attachment is recorded only for a start the runtime itself
            # calls ready. Anything else raises with the whole launch diagnosis
            # attached, having registered nothing -- a half-started Dispatch must not
            # become a row that later reads like a live supervised worker.
            if "state" not in result:
                # A success receipt with no `state` at all. Legitimate on exactly one
                # HISTORICAL point observation (see
                # WORKER_START_STATELESS_RECEIPT_VERSION -- no longer in the current
                # executable support set, so this branch is offline-covered only) and
                # missing lifecycle evidence everywhere else -- including on a
                # 1.4.196 runtime, where `state` is the field that carries the launch
                # outcome, and on a harness that never identified its runtime.
                # BUGFIX-I1-MAJOR-1: this branch used to be unconditional, which let a
                # malformed 1.4.196 receipt be written to the ledger as an adopted
                # supervised worker.
                if self.orca_app_version != WORKER_START_STATELESS_RECEIPT_VERSION:
                    raise OrcaRuntimeError(
                        "worker-start returned a success receipt with no launch "
                        "state; that shape is accepted only from the point-verified "
                        f"Orca {WORKER_START_STATELESS_RECEIPT_VERSION} runtime, and "
                        "this harness has validated "
                        f"{self.orca_app_version or 'no runtime'}: "
                        f"dispatchId={result.get('dispatchId')!r} result={result!r}"
                    )
            elif str(result.get("state")) != WORKER_START_READY_STATE:
                raise OrcaRuntimeError(
                    "worker-start did not reach a ready worker: "
                    f"state={str(result.get('state'))!r} "
                    f"stage={result.get('stage')!r} "
                    f"failedStage={result.get('failedStage')!r} "
                    f"lastError={result.get('lastError')!r} "
                    f"dispatchId={result.get('dispatchId')!r} "
                    f"residualResources={result.get('residualResources')!r}"
                )
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

    # ---- OS-17: run-scoped ORCHESTRATOR_LOG.md / TIMING_LOG.md -----------------
    # SKILL.md section 9 has always named these two files as something a run
    # leaves behind under its own <ARTIFACT_ROOT>, but nothing in the actual
    # execution path wrote them. These three helpers are the whole fix: every
    # call site below hands them a RuntimeAttempt (or a status string) this
    # harness already built for its own return value, so no new state is
    # invented for logging's sake. Every write goes through _safe_log so a
    # logging failure -- a full disk, an unwritable path -- is recorded in
    # self._logging_errors and never raised into the caller, which would
    # otherwise turn an already-settled Dispatch into an apparent failure.

    def _safe_log(self, writer: Any, *args: Any, **kwargs: Any) -> None:
        try:
            writer(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 -- see the section note above
            self._logging_errors.append(f"{getattr(writer, '__name__', writer)}: {error}")

    def _emit_timing_row(self, **fields: Any) -> None:
        """The writer RunTimingTracker emits phase/iteration boundary rows through.

        OS-19: injected rather than let the tracker write directly, so a boundary
        row is covered by the same _safe_log guarantee as every other row this
        class produces -- a failed write lands in self._logging_errors instead of
        unwinding into an already-settled Dispatch.
        """
        self._safe_log(
            run_logging.log_timing_event, self.run_id, base=self.artifact_dir, **fields
        )

    def _log_attempt(
        self,
        *,
        phase: str | None,
        attempt: "RuntimeAttempt",
        terminal_created: bool,
        started_at: str,
        ended_at: str,
        event: str = "dispatch_settled",
        round_kind: str = "phase_gate",
    ) -> None:
        """One ORCHESTRATOR_LOG.md row and one TIMING_LOG.md row for one attempt.

        The single call site every dispatch-producing path in this class shares
        (run_existing_task, observe_unexpected_exit): a Worker dispatch, a phase
        Reviewer dispatch, a correction round, a downstream revalidation round,
        and a Final Adversarial Review attempt are all just a RuntimeAttempt
        built through one of those two methods, so logging them here once
        answers section 2's seven questions without a second code path per
        event kind.
        """
        if not self.run_id:
            return
        # OS-3 TEST review F-001: the value is threaded explicitly from each dispatch
        # call site, never inferred from unrelated state, and validated here -- the
        # single funnel every settled dispatch passes through. run_logging owns the
        # vocabulary, so there is one list, not two.
        if round_kind not in run_logging.ROUND_KIND_VALUES:
            raise OrcaRuntimeError(
                f"unknown round_kind: {round_kind!r}; expected one of "
                f"{run_logging.ROUND_KIND_VALUES}"
            )
        # round 5 review MAJOR: the phase/iteration boundary for this attempt's
        # own (phase, iteration) is opened by the CALLER, before the dispatch
        # this attempt reports on ever started -- see _open_phase_iteration_
        # boundary()'s own docstring. By the time _log_attempt() runs, the
        # dispatch has already settled, so this method only ever RECORDS the
        # scope's ongoing state, never opens it.
        action = "created" if terminal_created else "reused"
        body_excerpt = " ".join((attempt.body or "").split())[:160]
        # OS-17 review: derived from attempt.role/attempt.body -- the same two
        # fields every call site of this method already populated by settlement --
        # not threaded in as new parameters, since nothing outside this method needs
        # to know either verdict before the write it belongs to.
        gate_result = _reviewer_gate_result(attempt.role, attempt.body or "")
        review_verdict = _reviewer_review_verdict(attempt.role, attempt.body or "")
        # OS-29: the settled attempt's own decision declaration becomes an immutable
        # ledger record and this row's two sparse columns. Done HERE because this is
        # the single funnel every settled dispatch passes; it can never be the GATE,
        # which is why the gate itself runs before start_worker instead.
        decision_state, decision_reason_code = self._record_decision_from_attempt(
            phase=phase, attempt=attempt, event=event
        )
        # The most recent reviewer-role gate result, and this attempt's own
        # ended_at, observed for the currently open iteration/phase become that
        # boundary's own eventual iteration_end/phase_end `detail`/`ended_at`
        # when it closes -- see _close_iteration_boundary()/_close_phase_
        # boundary(). A Worker attempt leaves the result unchanged (gate_result
        # == "") but still advances the scope's last-known end time.
        if self._timing is not None:
            self._timing.record_scope_activity(ended_at=ended_at, result=gate_result)
        self._safe_log(
            run_logging.log_orchestrator_event,
            self.run_id,
            base=self.artifact_dir,
            event=event,
            phase=phase or "",
            role=attempt.role,
            iteration=attempt.iteration,
            task_id=attempt.task_id,
            dispatch_id=attempt.dispatch_id,
            terminal=attempt.terminal,
            action=action,
            reuse=attempt.terminal_effect,
            gate_result=gate_result,
            review_verdict=review_verdict,
            risk=self.risk,
            # Written on unexpected_exit rows too: both events describe a dispatch,
            # and "which kind of round did this happen in" is exactly the question
            # OS-17's workflow-path requirement asks. Only pre_dispatch_failure --
            # which has no dispatch at all -- leaves it blank.
            round_kind=round_kind,
            decision_state=decision_state,
            decision_reason_code=decision_reason_code,
            result=(
                f"outcome={attempt.outcome} settlement={attempt.settlement} "
                f"lifecycle={attempt.lifecycle_action} "
                f"worker_resource={attempt.worker_resource}"
            ),
            detail=body_excerpt,
        )
        self._safe_log(
            run_logging.log_timing_event,
            self.run_id,
            base=self.artifact_dir,
            event=event,
            phase=phase or "",
            role=attempt.role,
            iteration=attempt.iteration,
            started_at=started_at,
            ended_at=ended_at,
            # OS-19: the duration is derived inside log_timing_event, which is
            # inside _safe_log. Deriving it HERE put a `(end - start)` outside
            # the logging guard, where a naive/aware timestamp pair raises
            # TypeError straight into an already-settled Dispatch -- the one
            # thing section 9 says logging may never do.
            risk=self.risk,
            detail=f"task={attempt.task_id} dispatch={attempt.dispatch_id}",
        )
        # OS-22: the Final Review dispatch's own audit record, written HERE --
        # after four-axis finalization, before the caller reads the verdict.
        # Deferring it to run end loses the report: task_context's
        # phase_artifact_contract() hands every attempt the same unsuffixed
        # FINAL_REVIEW.md, so attempt N+1's Reviewer can overwrite attempt N's.
        if round_kind == "final_review":
            self._log_final_review_audit(attempt=attempt, event=event)

    def _log_final_review_audit(
        self, *, attempt: "RuntimeAttempt", event: str
    ) -> None:
        """One immutable per-dispatch record for one Final Adversarial Review attempt.

        Every write goes through _safe_log, so a collision, an OSError at any
        staging boundary, or an unavailable capture lands in self._logging_errors as
        one log row and the run continues. An audit-write failure never mutates
        settled lifecycle state -- the same rule section 9 already states for the
        two logs, applied to the record family that now sits beside them.

        The ladder's rows 1 and 2 (a dispatch refused at input, an invalid or revoked
        capability) are deliberately reported as NOT observed from here rather than
        guessed at: a dispatch refused at input never reaches this method, because it
        never produces a settled RuntimeAttempt at all. A live Coordinator records
        those two through `final-review-audit-write --void-reason`, which is what
        that flag exists for.
        """
        if not self.run_id or attempt.role != "reviewer":
            return
        report_capture, report_parse = run_logging.probe_final_review_report(
            self.run_id, attempt.iteration, base=self.artifact_dir
        )
        provenance, void_reason, settlement = (
            run_logging.resolve_final_review_provenance(
                settled=attempt.settlement == "completed"
                and event == "dispatch_settled",
                report_capture_status=report_capture,
                report_parse_status=report_parse,
            )
        )
        entry = (
            self.agent_routing.for_role("final_review", "final_reviewer")
            if self.agent_routing is not None
            else None
        )
        self._safe_log(
            run_logging.write_final_review_audit_record,
            self.run_id,
            base=self.artifact_dir,
            final_review_attempt=attempt.iteration,
            task_id=attempt.task_id,
            dispatch_id=attempt.dispatch_id,
            provenance_state=provenance,
            void_reason=void_reason,
            settlement_state=settlement,
            reviewer_terminal=attempt.terminal,
            reviewer_agent_command=(
                entry.command if entry is not None and entry.resolved else ""
            ),
            reviewer_agent_origin=(
                entry.origin if entry is not None and entry.resolved else "unknown"
            ),
            # The runtime's OWN labels, verbatim. Never mapped into an enum, and
            # never compared against a threshold constant.
            failure_detail=(
                ""
                if provenance == run_logging.PROVENANCE_ACCEPTED
                else f"outcome={attempt.outcome} settlement={attempt.settlement} "
                f"dispatch_status={attempt.dispatch_status} event={event}"
            ),
        )

    def _b1_guard(
        self,
        *,
        phase: str | None,
        role: str,
        iteration: int,
        verifies: str | None = None,
    ) -> None:
        """A1-A6 over this run's ledger head. Raises DecisionGateRefused, or returns.

        A run this process never opened has no ledger of its own to read, so the
        guard is a no-op before start_run() -- there is no boundary to gate yet and
        no run root to read from.

        Final Adversarial Review F-001: this guard used to describe the dispatch to
        the gate as nothing at all, so A5 saw one anonymous caller and refused the
        PERMITTED current-phase verification Reviewer with the same
        `DECISION_BLOCKED:*` it owes a correction Worker or the next phase. The
        dispatch is now DESCRIBED -- role, phase, iteration and the `verifies`
        binding the caller is offering -- and decision_gate.admit_head() decides.
        Nothing about the refusal is decided here: this method still only logs and
        re-raises whatever the gate says, and `verifies` defaults to None, so every
        caller that offers no verification is gated exactly as before.

        When the gate admits a verification, the admitted HEAD is remembered as
        `_pending_verification` so the Reviewer's own B3 record can be bound to it
        and evaluated under the shared verification/downgrade rules. Every other
        admission clears it -- an ordinary round owes no verification, and a stale
        arming must never survive into one.
        """
        if not self.run_id:
            return
        try:
            head = decision_gate.admit_head(
                self._decision_policy,
                run_logging.read_decision_ledger(self.run_id, base=self.artifact_dir),
                run_id=self.run_id,
                expected_settled_round=self._last_settled,
                verification=decision_gate.VerificationDispatch(
                    role=role,
                    phase=phase or "",
                    iteration=iteration,
                    verifies=verifies,
                ),
            )
        except decision_gate.GateRefusal as refusal:
            error = DecisionGateRefused(refusal.reason, refusal.detail)
            self._log_pre_dispatch_failure(
                phase=phase, role=role, iteration=iteration, error=error
            )
            self._safe_log(
                run_logging.log_orchestrator_event,
                self.run_id,
                base=self.artifact_dir,
                event=run_logging.EVENT_DECISION_GATE_REFUSED,
                phase=phase or "",
                role=role,
                iteration=iteration,
                risk=self.risk,
                decision_state=decision_gate.decision_columns(refusal.reason)[0],
                decision_reason_code=decision_gate.decision_columns(refusal.reason)[1],
                detail=" ".join(refusal.detail.split())[:200],
            )
            raise error
        # An admission past an OPEN head is a verification and nothing else: the gate
        # only returns one when the head is that Worker's own open blocking B2 record
        # and this dispatch is bound to it. Any other admission means the head is
        # settled and closed, which owes no verification.
        if head.get("open_decision_item") is True and head.get(
            "state"
        ) in decision_gate.BLOCKING_STATES:
            self._pending_verification = _PendingVerification(
                worker_key=decision_gate.ledger_key(head),
                worker=decision_gate.GateResult(
                    declared_state=str(head.get("state", "")), record=head
                ),
                round=(self.run_id, phase or "", iteration),
            )
        else:
            self._pending_verification = None

    def _record_decision_from_attempt(
        self, *, phase: str | None, attempt: "RuntimeAttempt", event: str
    ) -> tuple[str, str]:
        """The settled attempt's own gate declaration -> a ledger record + two columns.

        Two cases, and the difference between them is the whole fail-closed
        behaviour this path can offer. A settled B2/B3 boundary that produced no
        usable gate result is NOT a third, tolerated case:

        * The body does not yield a valid gate result -- because it declared
          NOTHING AT ALL (the missing-record case, DECISION_GATE_INPUT_MISSING),
          or because what it declared is defective. No record is published and
          `_last_settled` IS advanced either way, so the very next B1 refuses with
          DECISION_GATE_INPUT_UNBOUND: the ledger head is still the round before
          this one, which no longer matches the round that actually settled.
          Silence poisons the next dispatch exactly as a broken declaration does;
          neither can ever pass for a good one, and neither is presumed CLEAR.
        * The body declares a valid gate result. The record is published, bound to
          the round that actually settled, and the columns carry it.

        Round 2 review F-001: an earlier version returned ("", "") and left
        `_last_settled` untouched when `declares_gate_result()` was false, so a
        legacy non-declaring agent left the following B1 still admitting the
        sequence-0 run-entry head as though no round had settled. That is the
        missing-decision-record case, which ORIGINAL_REQUEST's fail-closed list
        names first and forbids unconditionally; a legacy exception is not
        available here no matter how it is disclosed.
        """
        # B2/B3 are "after RECEIVING the Worker/Reviewer result". A dispatch that
        # delivered no result never reached either boundary, so there is no gate
        # result to be missing: an unexpected exit is a NON-RESPONSE, which the
        # lifecycle recovery path already owns (outcome=unknown, worker_done_count=0,
        # its own `unexpected_exit` event) and which this method must not convert
        # into a settled boundary. It is still never presumed CLEAR -- no record is
        # published, the columns stay blank, no gate_result is recorded, and the
        # recovery dispatch that follows is itself B1-guarded (round 2 review F-002).
        # This is a statement about WHICH attempts are gate boundaries, not a
        # tolerated shape of result at one; see the docstring above for why no
        # exception of the latter kind exists any more.
        if event != "dispatch_settled" or attempt.worker_done_count < 1:
            return "", ""
        body = attempt.body or ""
        settled = (self.run_id, phase or "", attempt.iteration)
        if not decision_gate.declares_gate_result(body):
            # Named separately from the parse failure below only so the reason code
            # says WHICH fail-closed clause fired; the effect is identical.
            self._last_settled = settled
            return decision_gate.INPUT_DEFECT_STATE, decision_gate.GATE_INPUT_MISSING
        try:
            gate = decision_gate.parse_gate_result(body, self._decision_policy)
        except decision_gate.GateRefusal as refusal:
            self._pending_verification = None
            self._last_settled = settled
            return decision_gate.INPUT_DEFECT_STATE, refusal.reason
        # ---- OS-29 B3-V. Consumed by exactly the Reviewer attempt of the round B1
        # armed, and cleared by every other settled attempt, so a verification cannot
        # be carried forward into a round that was never admitted as one.
        pending = self._pending_verification
        self._pending_verification = None
        verifying = (
            pending
            if pending is not None
            and attempt.role == "reviewer"
            and pending.round == settled
            else None
        )
        defect = self._verification_defect(verifying, gate, settled)
        if defect is not None:
            # Row 7, and its mirror image. An unbound verification record and a
            # `verifies` claim made outside verification mode are BOTH unbound, and
            # both are named rather than silently falling back to the Worker's
            # classification: the fall-back would hide the defect behind a block that
            # looks identical to a healthy one. Nothing is published, and the round is
            # bound anyway, so the next B1 refuses too.
            self._safe_log(
                run_logging.log_orchestrator_event,
                self.run_id,
                base=self.artifact_dir,
                event=run_logging.EVENT_DECISION_BLOCK,
                phase=phase or "",
                role=attempt.role,
                iteration=attempt.iteration,
                risk=self.risk,
                decision_state=decision_gate.INPUT_DEFECT_STATE,
                decision_reason_code=decision_gate.GATE_INPUT_UNBOUND,
                detail=" ".join(defect.split())[:200],
            )
            self._last_settled = settled
            return (
                decision_gate.INPUT_DEFECT_STATE,
                decision_gate.GATE_INPUT_UNBOUND,
            )
        record = dict(gate.record)
        record.update(
            {
                "ledger_schema_version": decision_gate.LEDGER_RECORD_SCHEMA_VERSION,
                "run": self.run_id,
                "phase": phase or "",
                "iteration": attempt.iteration,
                "role": "reviewer" if attempt.role.endswith("reviewer") else "worker",
                "boundary": "B3" if attempt.role.endswith("reviewer") else "B2",
                "source": "reviewer" if attempt.role.endswith("reviewer") else "worker",
                "verdict": _reviewer_gate_result(attempt.role, body),
                "verifies": record.get("verifies"),
                "prior_open_decision_items": [],
                "recorded_at": run_logging.now_iso(),
                # External review MAJOR: this was a setdefault, so an agent that put
                # `source_binding` in its own fenced record kept it -- an arbitrary,
                # null, cross-run or wrong-phase binding survived into the published
                # ledger, and validate_ledger_record() only requires the field to be
                # PRESENT, never that it matches the run this record belongs to. The
                # entry could therefore claim provenance it was never bound to. It is
                # harness-owned now, exactly like `run`, `phase`, `iteration` and
                # `recorded_at` beside it: the agent describes its DECISION, never
                # where that decision is recorded.
                "source_binding": f"artifacts/runs/{self.run_id}/",
            }
        )
        record.setdefault("responsible_phase", phase or "")
        record.setdefault("evidence", {})
        record.setdefault("assumption", None)
        record.setdefault("open_item", None)
        try:
            run_logging.append_decision_ledger_record(
                self.run_id,
                record,
                base=self.artifact_dir,
                ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION,
            )
        except Exception as error:  # noqa: BLE001 -- section 9: logging never gates
            # The write failed, so no record binds this round. `_last_settled` is
            # advanced anyway: the next B1 then refuses as UNBOUND, which is the
            # fail-closed direction. A logging failure may not turn a settled
            # dispatch into a failure, and it may not turn a refusal into an
            # admission either.
            self._logging_errors.append(f"append_decision_ledger_record: {error}")
        self._last_settled = settled
        if verifying is not None:
            # P6b rows 4-6, from the SHARED evaluator the deterministic harness uses.
            # The columns carry the VERIFICATION's terminal -- confirmation keeps the
            # Worker's own state and code, a stricter Reviewer carries its own, and a
            # downgrade is decided solely by decision_policy.validate_transition().
            # The round stays terminal either way: the Worker's item is still open
            # (open_items() lets no verification resolve anything), so the correction
            # Worker, the next phase, a second Reviewer and the Final Review all stay
            # refused at the next B1, and no correction iteration is charged.
            outcome = decision_gate.evaluate_verification(
                self._decision_policy, verifying.worker, gate
            )
            self._safe_log(
                run_logging.log_orchestrator_event,
                self.run_id,
                base=self.artifact_dir,
                event=run_logging.EVENT_DECISION_BLOCK,
                phase=phase or "",
                role=attempt.role,
                iteration=attempt.iteration,
                risk=self.risk,
                decision_state=outcome.block[0],
                decision_reason_code=outcome.block[1] or "",
                detail=f"{outcome.reason} verifies={verifying.worker_key}",
            )
            return decision_gate.decision_columns(outcome.reason)
        return str(record.get("state", "")), str(record.get("reason_code") or "")

    def _verification_defect(
        self,
        verifying: _PendingVerification | None,
        gate: "decision_gate.GateResult",
        settled: tuple[str, str, int],
    ) -> str | None:
        """Is this settled result's `verifies` field consistent with its mode?

        Two symmetric defects, one check, matching the deterministic harness:

        * in verification mode the record MUST carry a `verifies` reference bound to
          the admitted Worker record -- decision_gate.verification_binding_defect()
          is the same function E2EHarness.run() calls at B3-V;
        * outside verification mode a record must carry NONE. A Worker verifies
          nothing, and a normal-mode Reviewer has no classification to verify, so a
          `verifies` claim there is unbound rather than extra evidence.
        """
        if verifying is None:
            if gate.record.get("verifies") is None:
                return None
            return (
                "a `verifies` reference outside verification mode; `verifies` "
                "belongs to the already-scheduled Reviewer's B3 verification record"
            )
        return decision_gate.verification_binding_defect(
            gate,
            worker_key=verifying.worker_key,
            run_id=settled[0],
            phase=settled[1],
            iteration=settled[2],
        )

    def _log_pre_dispatch_failure(
        self, *, phase: str | None, role: str, iteration: int, error: Exception
    ) -> None:
        """A dispatch_context() render that raised before any Task existed.

        Section 5's "invalid quality profile 등 pre-dispatch failure": the
        BLOCKED-style rejection build_quality_gate_context() issues (an invalid
        profile, an undeclared requested_phases at the final gate) happens
        before task-create, so there is no Task/Dispatch id to attach this to
        -- only the phase/role/iteration the caller was about to dispatch.
        """
        if not self.run_id:
            return
        self._safe_log(
            run_logging.log_orchestrator_event,
            self.run_id,
            base=self.artifact_dir,
            event="pre_dispatch_failure",
            phase=phase or "",
            role=role,
            iteration=iteration,
            result="error",
            detail=" ".join(str(error).split())[:200],
        )

    def log_run_status(self, status: str, *, reason: str = "") -> None:
        """The one run-end log write: section 2's four terminal statuses.

        Status is validated eagerly and NOT through _safe_log: an unrecognized
        status is a caller bug (a typo'd literal), not an I/O failure, and OS-17
        section 5 only asks that a *logging* failure stay inert -- it does not
        ask this method to accept a status the contract does not define.
        """
        if status not in run_logging.RUN_STATUS_VALUES:
            raise run_logging.RunLoggingError(
                f"unknown run status: {status!r}; expected one of "
                f"{run_logging.RUN_STATUS_VALUES}"
            )
        if not self.run_id:
            return
        # ---- OS-29 pre-completion decision gate (external review CRITICAL) --------
        # The decision axis used to be enforced ONLY by _b1_guard(), i.e. only when a
        # LATER dispatch existed to gate. After the Final Review there is no later
        # dispatch, so a Final Reviewer could return quality PASS beside NEEDS_INPUT,
        # CONFLICT, or a missing/malformed declaration and this method would still
        # write COMPLETED -- exactly the completion the objective forbids while user
        # authority is unresolved. Completion is therefore its own boundary and
        # admits the ledger head under the SAME A1-A6 the dispatch boundaries use.
        #
        # No VerificationDispatch is offered: completing a run is never the one
        # permitted classification review, so A5 refuses every open item here as it
        # does for a correction Worker or the next phase.
        #
        # ONLY COMPLETED is gated. BLOCKED/ERROR/ESCALATED must stay writable or a
        # blocked run could never record why it stopped -- and this method is how it
        # records that, including on the refusal path immediately below.
        if status == "COMPLETED":
            records = run_logging.read_decision_ledger(
                self.run_id, base=self.artifact_dir
            )
            try:
                decision_gate.admit_head(
                    self._decision_policy,
                    records,
                    run_id=self.run_id,
                    expected_settled_round=self._last_settled,
                )
            except decision_gate.GateRefusal as refusal:
                # The refusal reason is this run's recorded TERMINAL classification,
                # so it must name what actually stopped the run. admit_head()
                # evaluates A6 before A5, and A6 disagrees for every item THIS RUN
                # opened, so a valid NEEDS_INPUT/CONFLICT would otherwise be recorded
                # as a producer defect and lose its state and reason code at the last
                # boundary (external re-review MAJOR).
                #
                # The refusal REASON is passed in, and only A6 is reclassified:
                # admit_head() evaluates A1 -> A2 -> A4 -> A3 -> A6, so reaching A6
                # proves the ledger-level clauses already passed. A structurally
                # invalid ledger -- duplicate or gapped sequences, a record from
                # another run, a head bound to the wrong phase or iteration -- can
                # still hold individually valid blocking records, and reclassifying
                # those would replace a real defect with a valid-looking block.
                reason = (
                    decision_gate.unresolved_block_reason(
                        self._decision_policy,
                        records,
                        refused_with=refusal.reason,
                    )
                    or refusal.reason
                )
                state, code = decision_gate.decision_columns(reason)
                detail = " ".join(str(refusal.detail).split())[:200]
                self._safe_log(
                    run_logging.log_orchestrator_event,
                    self.run_id,
                    base=self.artifact_dir,
                    event=run_logging.EVENT_DECISION_BLOCK,
                    risk=self.risk,
                    decision_state=state,
                    decision_reason_code=code,
                    detail=f"COMPLETED refused at the pre-completion gate: {detail}",
                )
                # Terminate BLOCKED instead of COMPLETED. This recurses exactly once:
                # BLOCKED is not gated, so the branch above is not re-entered.
                self.log_run_status("BLOCKED", reason=f"{reason}: {detail}")
                raise DecisionGateRefused(reason, refusal.detail)
        if status == "BLOCKED":
            self._publish_clarifications_for_terminal_block()
        self._safe_log(
            run_logging.log_run_status,
            self.run_id,
            status,
            base=self.artifact_dir,
            reason=reason,
            run_started_at=self._run_started_at,
            risk=self.risk,
            risk_source=self.risk_source,
        )

    def _publish_clarifications_for_terminal_block(self) -> None:
        if not self.run_id:
            return
        # One binding rule for the whole system: the OS-29 predicate, restated in
        # clarification_protocol so a forged inner `verifies` cannot be folded here
        # either. A local copy of this check is how the weaker variant survived.
        valid = clarification_protocol.canonical_reviewer_binding
        try:
            records = run_logging.read_decision_ledger(self.run_id, base=self.artifact_dir)
            sources = clarification_protocol.terminal_block_sources(
                run_id=self.run_id, records=records, coordinator_input=self.clarification_inputs,
                ledger_key=decision_gate.ledger_key, valid_reviewer_binding=valid)
            if sources:
                # See the e2e seam: promote() is the initial publication AND the
                # later dependency-ready promotion, so a dependent question is
                # actually asked once its predecessor carries an effective decision.
                self.human_approval_port.promote(run_id=self.run_id, sources=sources)
        except Exception as exc:  # publication cannot mutate status or dispatch
            keys = sorted(source.source_ledger_key for source in locals().get("sources", ()))
            error = json.dumps({"exception":type(exc).__name__,"message":str(exc),"ledger_keys":keys},
                               sort_keys=True,separators=(",",":"))
            self.clarification_errors.append(error)
            self._safe_log(
                run_logging.log_orchestrator_event, self.run_id,
                base=self.artifact_dir,
                event=run_logging.EVENT_CLARIFICATION_PUBLICATION_FAILED,
                result="BLOCKED", detail=error,
            )

    # ---- OS-17 review: automatic phase/iteration boundaries in TIMING_LOG ---------
    # OS-17's own timing contract (section 3) named "phase start/end" and
    # "iteration start/end" as separate line items from Worker/Reviewer/Final
    # Review dispatch duration -- not something a reader is meant to reconstruct
    # by grouping dispatch_settled rows. Round 3 review MAJOR: an earlier version
    # of this made phase_start/phase_end/iteration_start/iteration_end public
    # methods a scenario author had to remember to call -- and none of the real
    # scenario functions (run_runtime_scenarios(), run_final_review_runtime_scenario(),
    # etc.) ever did, so a real OrcaRuntimeHarness run never actually produced
    # these rows despite the methods existing and being unit-tested directly.
    # Centralized instead in the two dispatch-initiating methods that already
    # exist for every Worker/Reviewer/correction/downstream-revalidation/Final-
    # Review dispatch (run_existing_task(), observe_unexpected_exit()) -- nothing
    # else decides when a boundary opens or closes, and no caller can omit it
    # because no caller is asked to call anything.
    #
    # round 5 review MAJOR: opening must happen BEFORE the dispatch it brackets
    # starts, not after settlement -- _log_attempt() runs only once the dispatch
    # has already finished, so a boundary opened there would always start AFTER
    # the very work it claims to bracket. _open_phase_iteration_boundary() is
    # therefore called by the caller with its own pre-dispatch `opened_at`
    # timestamp, not computed here. Closing an OUTGOING scope on a transition
    # uses that scope's own *_last_ended_at (the ended_at of the last attempt
    # actually inside it, advanced by _log_attempt() on every settled attempt via
    # RunTimingTracker.record_scope_activity()) -- never "now", which at
    # transition time is really "whenever the NEW scope's dispatch happened to
    # settle" and would silently pull the new scope's own work into the outgoing
    # scope's duration. Timing rows only -- ORCHESTRATOR_LOG.md already carries
    # phase and iteration on every dispatch_settled row, so a duplicate row there
    # would answer a question that row shape already answers.
    #
    # OS-19: those transitions now live in run_logging.RunTimingTracker and the
    # three methods below are thin delegations. The rules did not change; what
    # changed is that the CLI path -- a live Coordinator, one process per event,
    # which had NO boundary lifecycle and therefore wrote every iteration_end/
    # phase_end row of the real OS-3 run with an empty started_at and a blank
    # duration_s -- now runs the same ones out of the same class.

    def _open_phase_iteration_boundary(
        self, phase: str, iteration: int, *, opened_at: str
    ) -> None:
        """Open this dispatch's phase/iteration scope. Delegates to the tracker.

        OS-19: the transitions themselves moved into
        run_logging.RunTimingTracker so the CLI path -- which had no boundary
        lifecycle at all, and whose every iteration_end/phase_end row in the
        real OS-3 run therefore had an empty started_at and a blank duration --
        runs the same ones. This method stays because it is the name both
        dispatch-initiating call sites already use, and because `opened_at` is
        still the caller's own pre-dispatch timestamp rather than a second clock
        read taken here.
        """
        if self._timing is None or not self.run_id or not phase:
            return
        self._timing.open_boundary(phase, iteration, opened_at=opened_at)

    def _close_iteration_boundary(self, *, ended_at: str | None = None) -> None:
        if self._timing is None:
            return
        self._timing.close_iteration(ended_at=ended_at)

    def _close_phase_boundary(self, *, ended_at: str | None = None) -> None:
        if self._timing is None:
            return
        self._timing.close_phase(ended_at=ended_at)

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
        round_kind: str = "phase_gate",
        verifies: str | None = None,
    ) -> tuple[RuntimeAttempt, str]:
        """Dispatch and settle a Task that already exists (the graph-first path).

        Identical to run_attempt except that the Task is NOT created here.

        `phase` is the workflow stage and is passed straight through to
        dispatch_context, which refuses a missing or unknown one. A caller that
        rendered the spec itself must pass the SAME phase and evidence it rendered
        with: this re-render is the text that actually reaches worker-start, so a
        caller that supplied one and not the other would dispatch a different
        boundary than the one it created the Task with.

        `verifies` is the ledger key of the Worker B2 record this dispatch is being
        sent to VERIFY, and it is meaningful only for the already-scheduled
        current-phase Reviewer at MEDIUM/HIGH after that Worker recorded NEEDS_INPUT
        or CONFLICT (OS-29 P6b row 2). Leaving it None -- the default, and what every
        ordinary round passes -- means "this dispatch offers no verification", which
        after an open blocking head is refused exactly as before. Supplying it is not
        an admission either: decision_gate.admit_head() still requires the head to be
        that same Worker record, in this same run, phase and iteration, still open,
        the only thing open, and not already verified.
        """
        # ---- OS-29 B1. BEFORE dispatch_context, before any terminal is created and
        # before start_worker: an unresolved blocking decision must leave no Task, no
        # Dispatch and no terminal behind. This is the one OS-29 guarantee the live
        # path can enforce structurally; the rest of the gate is the Coordinator's
        # documented obligation (the decision gate contract block in SKILL.md), for
        # the reason recorded in this class's own docstring -- there is no
        # deterministic in-process iteration counter here (L7/R-11).
        # `verifies` is the ONE thing that can make this dispatch admissible past an
        # open blocking head, and it is offered by the CALLER -- the Coordinator that
        # already scheduled this Reviewer and knows which Worker record it is sending
        # it to verify. It is not derived here from the ledger: deriving it would let
        # the guard manufacture its own permission from the very file it is checking.
        self._b1_guard(
            phase=phase, role=role, iteration=iteration, verifies=verifies
        )
        # Before the dispatch, not after it. `spec` is what start_worker sends on the
        # low-level path and what a caller passes to task-create on the supervised
        # one, so the boundary has to be inside it by the time either happens.
        try:
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
                quality_profile=self.quality_profile,
                requested_phases=self.requested_phases,
                risk=self.risk,
                risk_source=self.risk_source,
                agent_routing=self.agent_routing,
            )
        except (TaskContextError, OrcaRuntimeError) as error:
            # OS-17 section 5: a pre-dispatch failure (invalid profile, an
            # undeclared requested_phases at the final gate) happens before any
            # Task exists. Log it, then re-raise unchanged -- this is logging
            # ABOUT the failure, not a recovery from it.
            self._log_pre_dispatch_failure(
                phase=phase, role=role, iteration=iteration, error=error
            )
            raise
        created_here = terminal is None
        handle = terminal or self.create_fake_terminal(
            role,
            mode,
            iteration=iteration,
            findings=findings,
            resolutions=resolutions,
            max_dispatches=max_dispatches,
            ask_before=ask_before,
            phase=phase,
        )
        dispatch_started_at = run_logging.now_iso()
        # round 5 review MAJOR: opened here, before start_worker(), so phase_start/
        # iteration_start actually brackets this dispatch instead of trailing it.
        self._open_phase_iteration_boundary(
            phase or "", iteration, opened_at=dispatch_started_at
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
        dispatch_ended_at = run_logging.now_iso()

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
        # Parsed out of `spec` -- the string that reached task-create and worker-start
        # -- for the same reason the two above are taken from the rendered payload.
        attempt.quality_gate = tuple(sorted(parse_quality_gate(spec).items()))
        self._log_attempt(
            phase=phase,
            attempt=attempt,
            terminal_created=created_here,
            started_at=dispatch_started_at,
            ended_at=dispatch_ended_at,
            round_kind=round_kind,
        )
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
        round_kind: str = "phase_gate",
        verifies: str | None = None,
    ) -> tuple[RuntimeAttempt, str]:
        """Create the Task, then run it. Return type and behavior unchanged.

        The two new keyword-only parameters both default, so every existing keyword
        call site still binds; `phase` then fails closed inside dispatch_context
        rather than falling back to `mode`.
        """
        # The Task spec is write-once, and on the supervised path it is the ONLY text
        # the agent sees (Orca replays it into the preamble), so it is composed here
        # rather than in run_existing_task, which meets an already-created Task.
        try:
            spec, _, _ = dispatch_context(
                role,
                iteration,
                mode,
                phase=phase,
                findings=findings,
                resolutions=resolutions,
                evidence=evidence,
                run_id=self.run_id or "",
                quality_profile=self.quality_profile,
                requested_phases=self.requested_phases,
                risk=self.risk,
                risk_source=self.risk_source,
                agent_routing=self.agent_routing,
            )
        except (TaskContextError, OrcaRuntimeError) as error:
            # Same OS-17 pre-dispatch-failure logging as run_existing_task's own
            # dispatch_context() call below -- this one runs first on this path
            # and never reaches run_existing_task if it raises.
            self._log_pre_dispatch_failure(
                phase=phase, role=role, iteration=iteration, error=error
            )
            raise
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
            round_kind=round_kind,
            verifies=verifies,
        )

    @staticmethod
    def _runtime_exit_report_defects(
        message: dict[str, Any], *, dispatch_id: str, task_id: str, terminal: str
    ) -> list[str]:
        """Every way `message` fails to be Orca's own unexpected-exit report, named.

        Returns [] only for a message that matches the live 1.4.196 receipt in EVERY
        checked respect. Never short-circuits: a caller (and a negative test) sees each
        failing clause by name, so a clause that stops being enforced is caught by the
        name that stops appearing.

        WHAT PROVES WHAT. Exactly one clause is authorship evidence: the top-level
        stored `sender_pane_key`, required present-and-null. Every other clause --
        type, subject, priority, taskId, dispatchId, handle, exitCode, exitCause -- is
        settable by a dispatched agent through `orchestration send`
        (`--type/--subject/--priority/--payload`), so they are IDENTITY AND SHAPE
        VALIDATION plus defence in depth, never proof of who wrote the message. Reading
        them as authorship is the error that made an earlier revision accept a
        full-shape worker-authored escalation.

        Nothing here is guessed. Every required field is present in the real receipt;
        every VALUE constraint is a type/shape constraint rather than an allowlist of
        observed values, because only one exit cause has been observed and pinning
        `kind == "unknown"` would reject a genuine report of a different cause.
        (BUGFIX-I1-MAJOR-2.)
        """
        defects: list[str] = []
        if message.get("type") != RUNTIME_EXIT_REPORT_TYPE:
            defects.append(f"type={message.get('type')!r}")
        subject = message.get("subject")
        if not isinstance(subject, str) or not subject.startswith(
            RUNTIME_EXIT_REPORT_SUBJECT_PREFIX
        ):
            defects.append(f"subject={subject!r}")
        if message.get("priority") != RUNTIME_EXIT_REPORT_PRIORITY:
            defects.append(f"priority={message.get('priority')!r}")
        # ---- authorship. Checked by PRESENCE first, then by value: a message that
        # omits the field is refused rather than read as the runtime's null.
        if "sender_pane_key" not in message:
            defects.append("sender_pane_key absent")
        elif message["sender_pane_key"] is not RUNTIME_EXIT_REPORT_SENDER_PANE_KEY:
            defects.append(f"sender_pane_key={message['sender_pane_key']!r}")

        try:
            payload = json.loads(message.get("payload") or "null")
        except (TypeError, ValueError):
            return defects + ["unparseable payload"]
        if not isinstance(payload, dict):
            return defects + [f"payload is {type(payload).__name__}, not an object"]

        # ---- identity: the two ids the round is bound to, plus the terminal.
        if payload.get("taskId") != task_id:
            defects.append(f"taskId={payload.get('taskId')!r} != {task_id!r}")
        if payload.get("dispatchId") != dispatch_id:
            defects.append(
                f"dispatchId={payload.get('dispatchId')!r} != {dispatch_id!r}"
            )
        # A third IDENTITY binding, not an authorship one: the runtime names the
        # terminal whose process ended, and it must be the terminal this observation
        # is about. An agent can set this field too (`--payload`), so it narrows WHICH
        # round a message may speak for; it never establishes WHO wrote it.
        if payload.get("handle") != terminal:
            defects.append(f"handle={payload.get('handle')!r} != {terminal!r}")

        # ---- the payload SHAPE the captured runtime report carries. Forgeable via
        # `--payload`, so this is malformed-report rejection and defence in depth --
        # not evidence of runtime authorship.
        exit_code = payload.get("exitCode")
        # bool is a subclass of int and is NOT an exit code.
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            defects.append(f"exitCode={exit_code!r}")
        cause = payload.get("exitCause")
        if not isinstance(cause, dict):
            defects.append(f"exitCause={cause!r}")
        else:
            for field_name in ("kind", "reason"):
                value = cause.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    defects.append(f"exitCause.{field_name}={value!r}")
        return defects

    def _classify_exit_checkpoint(
        self,
        checkpoint: dict[str, Any],
        *,
        dispatch_id: str,
        task_id: str,
        terminal: str,
    ) -> list[dict[str, Any]]:
        """Split an unexpected-exit checkpoint into runtime exit reports and violations.

        Returns the runtime's own exit reports for THIS dispatch, on THIS terminal.
        Raises on anything else, which keeps the scenario's actual claim intact: this
        dispatch produced no lifecycle result of its own.

        A message is admitted only by satisfying every clause in
        _runtime_exit_report_defects(): the identity and shape clauses -- type, subject
        wording, priority, the three identity fields, the exitCode/exitCause schema --
        AND the top-level stored `sender_pane_key`. Only the last is authorship
        evidence; every other clause is reproducible by a dispatched agent through
        `orchestration send --payload/--subject/--priority` and is kept as defence in
        depth. An escalation that merely names the right round, which is exactly what a
        fake agent's own `Blocked:` escalation does, is a violation and not a pass.

        An empty delivery returns [] and is equally correct: that is what Orca 1.4.184
        produces for the same scenario, and both point verifications must hold.
        """
        violations: list[str] = []
        reports: list[dict[str, Any]] = []
        for message in checkpoint.get("messages") or ():
            defects = self._runtime_exit_report_defects(
                message, dispatch_id=dispatch_id, task_id=task_id, terminal=terminal
            )
            if defects:
                violations.append(
                    f"{message.get('id')} is not this dispatch's runtime exit report ("
                    + ", ".join(defects)
                    + ")"
                )
            else:
                reports.append(message)
        if violations:
            raise OrcaRuntimeError(
                "unexpected exit produced a lifecycle message: " + "; ".join(violations)
            )
        return reports

    def observe_unexpected_exit(
        self,
        role: str,
        iteration: int,
        *,
        phase: str | None = None,
        round_kind: str = "phase_gate",
    ) -> RuntimeAttempt:
        """The second of the two centralized dispatch initiators.

        Round 2 review F-002: this path creates a Task, a terminal and a Dispatch
        exactly as run_existing_task() does, so it carries the SAME OS-29 B1
        obligation. It previously reached start_worker() without consulting the
        ledger at all, which let an unresolved, malformed, unsupported-schema or
        unbound head reach worker-start through here while the sibling path
        refused it.
        """
        # ---- OS-29 B1. Before dispatch_context, before create_task, before the
        # terminal and before start_worker -- the same "no Task, no Dispatch, no
        # terminal" placement run_existing_task() uses, and ahead of
        # _open_phase_iteration_boundary() so a refused dispatch opens no scope.
        #
        # No `verifies` is offered and none may be: a recovery dispatch after a
        # non-response is not the already-scheduled Reviewer verifying a
        # classification, so this initiator stays refused after ANY open blocking
        # head. The B3-V exception has exactly one entry point (F-001).
        self._b1_guard(phase=phase, role=role, iteration=iteration)
        dispatch_started_at = run_logging.now_iso()
        try:
            spec, _, _ = dispatch_context(
                role,
                iteration,
                "exit",
                phase=phase,
                base_spec=f"{role} iteration {iteration}: unexpected exit",
                run_id=self.run_id or "",
                quality_profile=self.quality_profile,
                requested_phases=self.requested_phases,
                risk=self.risk,
                risk_source=self.risk_source,
                agent_routing=self.agent_routing,
            )
        except (TaskContextError, OrcaRuntimeError) as error:
            self._log_pre_dispatch_failure(
                phase=phase, role=role, iteration=iteration, error=error
            )
            raise
        # round 5 review MAJOR: opened only once dispatch_context() has actually
        # succeeded (a pre-dispatch failure above never opens a boundary), and
        # before start_worker() -- same placement rule as run_existing_task().
        self._open_phase_iteration_boundary(
            phase or "", iteration, opened_at=dispatch_started_at
        )
        task_id = self.create_task(spec)
        handle = self.create_fake_terminal(
            role, "exit", iteration=iteration, phase=phase
        )
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
        exit_reports = self._classify_exit_checkpoint(
            checkpoint, dispatch_id=dispatch_id, task_id=task_id, terminal=handle
        )
        if exit_reports:
            # The delivery held only the runtime's own identity-bound exit report(s);
            # acknowledge it so the run mailbox does not replay it into the next
            # observation of this same run (guide: process the whole Delivery, then
            # acknowledge).
            self._signals.extend(RUNTIME_EXIT_REPORT_TYPE for _ in exit_reports)
            self._ack(checkpoint["deliveryId"])
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
        self._log_attempt(
            phase=phase,
            attempt=attempt,
            terminal_created=True,
            started_at=dispatch_started_at,
            ended_at=run_logging.now_iso(),
            event="unexpected_exit",
            round_kind=round_kind,
        )
        return attempt

    def finish(self, result: RuntimeScenarioResult) -> RuntimeScenarioResult:
        assert self.run_id and self.run_owner
        result.signals = list(self._signals)
        result.run_owner_handle = self.run_owner
        # PR #29 review MAJOR-1. The runtime IDENTITY this result was produced on,
        # carried on the result (and therefore into the snapshot on disk) so an
        # assertion can be bound to the runtime point it was validated for instead of
        # being inferred from the outcome it observed. `self.orca_app_version` is
        # written by preflight() ONLY after validate_orca_contract() accepted the
        # runtime, so this is a point-verified identity or the empty string -- and an
        # empty string is "no runtime proven", which every consumer must fail on.
        result.orca_app_version = self.orca_app_version
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
        # OS-17 review round 4 MAJOR: whatever phase/iteration boundary is still
        # open when the run ends (the common case -- nothing in this class's
        # normal flow closes the LAST phase/iteration itself, since there is no
        # next attempt whose transition would trigger it) closes here, before the
        # run's own terminal status is logged. round 5 review MAJOR: closed at
        # that scope's own last recorded activity (*_last_ended_at), not "now" --
        # snapshot-writing and the other bookkeeping just above this point is not
        # part of the phase/iteration's own work.
        if self._timing is not None:
            self._timing.close_all()
        # OS-17: the one call site every scenario already reaches on its way out,
        # so "log the run's terminal status" does not need a matching reminder
        # in each of them. `result.status` is one of run_logging.RUN_STATUS_VALUES
        # for every scenario this harness defines (COMPLETED/BLOCKED/ERROR/
        # ESCALATED); log_run_status() still fails closed if that ever stops
        # being true, before self.run_id is cleared below.
        self.log_run_status(result.status, reason="; ".join(result.recovery))
        self.run_owner = None
        self.run_id = None
        self._timing = None
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
RISK_LEVELS = ("low", "medium", "high")
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
        round_kind="correction",
    )
    reviewer2, _ = harness.run_attempt(
        "reviewer",
        2,
        "pass",
        phase=LIFECYCLE_SCENARIO_PHASE,
        terminal=reviewer_terminal,
        round_kind="correction",
    )
    results.append(harness.finish(RuntimeScenarioResult("B", run_id, "COMPLETED", 2, [worker, reviewer1, correction, reviewer2])))

    results.append(_scenario_c(harness))

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

    run_id = harness.start_run(
        "Final Adversarial Review scenario J terminal freshness",
        requested_phases=(LIFECYCLE_SCENARIO_PHASE,),
    )
    worker, _ = harness.run_attempt("worker", 1, "complete", phase=LIFECYCLE_SCENARIO_PHASE)
    phase_reviewer, phase_reviewer_terminal = harness.run_attempt(
        "reviewer", 1, "pass", phase=LIFECYCLE_SCENARIO_PHASE
    )
    # attempt 1: a brand-new terminal. terminal= is NOT passed - that is the scenario.
    # The phase is the gate itself, not the phase under review: a Final Adversarial
    # Review reads the whole run, and its boundary says so.
    final_1, final_terminal_1 = harness.run_attempt(
        "reviewer",
        1,
        "fail",
        phase=FINAL_REVIEW_PHASE,
        findings=("R1",),
        round_kind="final_review",
    )
    correction, _ = harness.run_attempt(
        "worker",
        2,
        "correction",
        phase=LIFECYCLE_SCENARIO_PHASE,
        resolutions={"R1": "RESOLVED"},
        round_kind="correction",
    )
    # attempt 2: another brand-new terminal, again with no terminal= argument.
    final_2, final_terminal_2 = harness.run_attempt(
        "reviewer", 2, "pass", phase=FINAL_REVIEW_PHASE, round_kind="final_review"
    )

    result = RuntimeScenarioResult(
        "J", run_id, "COMPLETED", 2,
        [worker, phase_reviewer, final_1, correction, final_2],
    )
    result.final_review_terminals = [final_terminal_1, final_terminal_2]
    result.phase_reviewer_terminals = [phase_reviewer_terminal]
    return harness.finish(result)


QUALITY_PROFILE_SCENARIO_PROFILE = """version: 1

quality_attributes:

  - id: DESIGN-001
    category: platform-infrastructure
    name: Design only rule
    blocking: false
    applies_to:
      - design

  - id: DOMAIN-001
    category: business-domain
    name: Idempotent processing
    blocking: true
    applies_to:
      - implementation
      - test

  - id: TEAM-001
    category: team-convention
    name: Repository convention
    blocking: false
"""


def run_quality_profile_runtime_scenario(
    artifact_dir: Path, *, harness: OrcaRuntimeHarness | None = None
) -> RuntimeScenarioResult:
    """Opt-in scenario L: phase filtering and one run-scoped profile, against Orca.

    Deliberately NOT part of run_runtime_scenarios(): that function's A-I result set is
    pinned by an exact-set assertion in test_orca_runtime.py. Scenarios J and K set the
    precedent; L follows it.

    The profile is written under `artifact_dir`, never into the repository being
    tested: installing one at the real .orca/quality-profile.yaml would change how
    every other run of this repository is reviewed, which is not a test's decision to
    make.

    `harness` exists so the scenario BODY can be executed offline by the contract
    tests. Everything that could be wrong here -- the attempt sequence, the phases, the
    assertions -- runs in both modes; only preflight and the environment dump are
    skipped when a harness is injected, and those are copied verbatim from scenarios J
    and K.
    """
    profile_root = artifact_dir / "quality-profile-project"
    (profile_root / ".orca").mkdir(parents=True, exist_ok=True)
    (profile_root / ".orca" / "quality-profile.yaml").write_text(
        QUALITY_PROFILE_SCENARIO_PROFILE, encoding="utf-8"
    )
    if harness is None:
        harness = OrcaRuntimeHarness(artifact_dir, quality_profile_root=profile_root)
        preflight = harness.preflight()
        (artifact_dir / "environment-quality-profile.json").write_text(
            json.dumps(preflight, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    # The scenario owns the profile its run is judged against, in both modes: an
    # injected harness would otherwise resolve whatever its constructor was pointed
    # at and quietly run the whole scenario against an absent profile.
    harness.quality_profile_root = profile_root

    run_id = harness.start_run(
        "Quality profile scenario L phase filtering",
        requested_phases=("design", "implementation"),
    )
    attempts = [
        harness.run_attempt("worker", 1, "complete", phase="design")[0],
        harness.run_attempt("reviewer", 1, "pass", phase="design")[0],
        harness.run_attempt("worker", 1, "complete", phase="implementation")[0],
        harness.run_attempt("reviewer", 1, "pass", phase="implementation")[0],
        harness.run_attempt(
            "reviewer", 1, "pass", phase=FINAL_REVIEW_PHASE, round_kind="final_review"
        )[0],
    ]

    result = RuntimeScenarioResult("L", run_id, "COMPLETED", 1, attempts)
    result.quality_profile_status = harness.quality_profile.status
    boundaries = [dict(attempt.task_boundary) for attempt in attempts]
    result.quality_profile_attributes = {
        f"{boundary['current_phase']}:{boundary['current_role']}":
            dict(attempt.quality_gate)["applicable_quality_attributes"]
        for attempt, boundary in zip(attempts, boundaries)
    }
    return harness.finish(result)


def run_session_reuse_runtime_scenario(artifact_dir: Path) -> RuntimeScenarioResult:
    """Opt-in scenario K: what the production reuse gate ANSWERS across five phases.

    ON ORCA 1.4.196 THIS SCENARIO VERIFIES THAT REUSE IS CORRECTLY REFUSED on the
    tracked path. It does NOT verify that session reuse works, and it must never be
    described as doing so. **Supervised session reuse is NOT VERIFIED on Orca
    1.4.196.** That claim belongs to the HISTORICAL Orca 1.4.184 point observation of
    an older harness revision -- see HISTORICAL_ORCA_APP_VERSION_OBSERVATIONS -- and to
    the offline contract suite; see docs/COMPATIBILITY.md.

    PR #29 review MAJOR-1: the eight refusals below are what the caller's assertion is
    now BOUND to, keyed on `result.orca_app_version`. The result carries the identity
    finish() copies off the harness so the expectation follows the runtime point
    rather than the observed outcome.

    Deliberately NOT part of run_runtime_scenarios(): that function's A-I result set
    is pinned by an exact-set assertion in test_orca_runtime.py, which this change may
    not edit. Scenario J set the precedent; K follows it.

    The whole chain runs inside ONE scenario because finish() clears the terminal
    ledger: a chain that spanned two scenarios would lose the owner_dispatch_ids the
    reuse aggregates are derived from.

    What `terminal=` each attempt gets is NOT decided by loop position: every attempt
    after the first of a role asks terminal_for_next_dispatch(), which takes a fresh
    observation of the previous dispatch and runs the eight-condition gate. That call
    is the point of the scenario, and the gate's ANSWER is the result under test --
    granted or refused.

    WHAT THE 1.4.196 RUN ACTUALLY PRODUCED (artifacts/orca-runtime/os41-final/
    scenario-k.json, and reproduced on every run since):

      * 8 gate decisions, ALL `eligible: false`;
      * each refusing with exactly these four condition names --
        ownership_not_transferable, release_state_missing,
        terminal_effect_unrecorded, worker_state_not_reusable;
      * `terminal_creations: 10` and ten DISTINCT attempt terminals, because each
        refusal returns None and the attempt opens a FRESH terminal.

    Ten terminals for ten dispatches, not two. The four refusals above are the
    conditions whose evidence exists only for a SUPERVISED dispatch
    (`worker.state`, `terminalResource.releaseState`/`ownershipState`, the
    worker-start terminal effect). On 1.4.196 every fake-agent dispatch is TRACKED --
    `worker.state` reads "unsupervised" with no terminalResource at all -- so the gate
    fails closed, correctly and by its own documented design. reuse_eligible() is NOT
    widened to accept tracked evidence; it is byte-identical to main.

    Every attempt but the last of each role still settles with lifecycle="reuse",
    which issues zero lifecycle commands (W-15 / W-16); on the tracked path that is
    recorded as `reuse:tracked-external` and the session is simply not handed onward,
    because the gate declined it. The last attempt of each role settles with
    lifecycle="release", recorded as `release:natural-exit`.

    The assertions in test_orca_runtime.py are written to hold on EITHER answer and to
    require the terminal accounting to follow the gate in both directions, so a
    runtime that granted reuse would still have to produce the reused chains.
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
    decisions: list[dict[str, Any]] = []

    def next_terminal(previous: RuntimeAttempt | None, role: str) -> str | None:
        """Ask the production gate which terminal the next attempt runs on.

        `role` is the intended role of the attempt about to be dispatched, spelled
        exactly as create_fake_terminal spells it when it registers the row, so a
        role swap really is a mismatch rather than a value copied out of the row it
        is being compared against. The agent command is the one the ledger recorded
        for the running session: a fresh session is started with a budget for the
        phases that REMAIN, so the command a reused terminal needs IS the command it
        is already running. No new constant.

        OS-41: the gate's answer is recorded either way. On a runtime where the gate's
        supervised worker-resource evidence exists it grants reuse; on one where the
        dispatch is tracked rather than supervised that evidence does not exist and the
        gate refuses, which is the correct fail-closed answer and is the thing under
        test there. Neither outcome is decided here -- this only asks and records.
        """
        if previous is None:
            return None
        handle = harness.terminal_for_next_dispatch(
            previous.terminal,
            role="phase_reviewer" if role.endswith("reviewer") else "phase_worker",
            agent_command=harness.ledger_terminal(previous.terminal)["agent_command"],
            dispatch_id=previous.dispatch_id,
        )
        if harness.last_reuse_decision is not None:
            decisions.append(dict(harness.last_reuse_decision))
        return handle

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
            quality_profile=harness.quality_profile,
            requested_phases=harness.requested_phases,
            risk=harness.risk,
            risk_source=harness.risk_source,
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
            # OS-41: a budget for the phases that REMAIN, not for the whole chain.
            # The fake agent exits once it has served this many dispatches, and that
            # exit is the only release receipt the tracked path has. With the whole
            # chain's count, a session that the reuse gate declines to hand onward is
            # left holding an unspent budget and never exits, so the final
            # `release` could not be observed. Remaining-count is correct on both
            # answers: a granted chain still gets its full budget at the first
            # attempt, and a refused one gets a fresh session whose budget is exactly
            # the one dispatch it will serve.
            max_dispatches=len(phases) - iteration + 1,
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
            quality_profile=harness.quality_profile,
            requested_phases=harness.requested_phases,
            risk=harness.risk,
            risk_source=harness.risk_source,
        )
        # OS-3 (site 2 of the verdict table): EXCLUDED from create_phase_graph -- this
        # fixture deliberately has no dependency edge and deliberately builds the
        # reviewer spec AFTER the worker settles, which is the property it exists to
        # demonstrate. The risk conditional therefore lives at the caller instead.
        if harness.risk == "low":
            harness.log_reviewer_gate_skipped(phase)
            worker_previous = worker
            attempts.append(worker)
            continue
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
            # OS-41: a budget for the phases that REMAIN, not for the whole chain.
            # The fake agent exits once it has served this many dispatches, and that
            # exit is the only release receipt the tracked path has. With the whole
            # chain's count, a session that the reuse gate declines to hand onward is
            # left holding an unspent budget and never exits, so the final
            # `release` could not be observed. Remaining-count is correct on both
            # answers: a granted chain still gets its full budget at the first
            # attempt, and a refused one gets a fresh session whose budget is exactly
            # the one dispatch it will serve.
            max_dispatches=len(phases) - iteration + 1,
        )
        if reviewer.outcome == "succeeded":
            approved_baseline.append(worker_artifact)
        worker_previous, reviewer_previous = worker, reviewer
        attempts.extend((worker, reviewer))

    result = RuntimeScenarioResult("K", run_id, "COMPLETED", len(phases), attempts)
    result.reuse_decisions = decisions
    result.phase_reviewer_terminals = [
        reviewer_previous.terminal if reviewer_previous else ""
    ]
    return harness.finish(result)


def run_risk_runtime_scenario(artifact_dir: Path) -> list[RuntimeScenarioResult]:
    """OS-3: the section 6 graph shape, asserted on the MIGRATED path.

    Runs the migrated _scenario_g site twice -- once at LOW, once at MEDIUM -- and
    records what the run's REAL task list contained, not what the helper returned.
    At LOW there must be no phase Reviewer task at all; at MEDIUM the Reviewer task
    must be pending before the Worker settles and ready after. The section 17 Final
    Review task is created at both levels and never routes through the helper.
    """
    results: list[RuntimeScenarioResult] = []
    for risk in ("low", "medium"):
        harness = OrcaRuntimeHarness(artifact_dir, risk=risk, risk_source="explicit")
        harness.preflight()
        run_id = harness.start_run(
            f"OS-3 risk scenario ({risk})",
            requested_phases=(LIFECYCLE_SCENARIO_PHASE,),
        )
        worker_spec = dispatch_context(
            "worker",
            1,
            "complete",
            phase=LIFECYCLE_SCENARIO_PHASE,
            run_id=run_id,
            quality_profile=harness.quality_profile,
            requested_phases=harness.requested_phases,
            risk=harness.risk,
            risk_source=harness.risk_source,
        )[0]
        reviewer_spec = dispatch_context(
            "reviewer",
            1,
            "pass",
            phase=LIFECYCLE_SCENARIO_PHASE,
            run_id=run_id,
            quality_profile=harness.quality_profile,
            requested_phases=harness.requested_phases,
            risk=harness.risk,
            risk_source=harness.risk_source,
        )[0]
        worker_task, reviewer_task = harness.create_phase_graph(
            worker_spec, reviewer_spec
        )
        result = RuntimeScenarioResult("R", run_id, "COMPLETED", 1)
        result.risk = harness.risk
        result.risk_source = harness.risk_source
        if reviewer_task is None:
            harness.log_reviewer_gate_skipped(LIFECYCLE_SCENARIO_PHASE)
            result.reviewer_gates_skipped = [LIFECYCLE_SCENARIO_PHASE]
        else:
            result.phase_reviewer_task_ids = [reviewer_task]
            result.reviewer_task_status = harness.task_status(reviewer_task)
        worker, _ = harness.run_existing_task(
            "worker",
            1,
            "complete",
            worker_task,
            phase=LIFECYCLE_SCENARIO_PHASE,
            spec=worker_spec,
        )
        result.attempts.append(worker)
        if reviewer_task is not None:
            result.reviewer_task_status = harness.task_status(reviewer_task)
            reviewer, _ = harness.run_existing_task(
                "reviewer",
                1,
                "pass",
                reviewer_task,
                phase=LIFECYCLE_SCENARIO_PHASE,
                spec=reviewer_spec,
            )
            result.attempts.append(reviewer)
        harness.log_run_status("COMPLETED")
        results.append(harness.finish(result))
    return results


def _scenario_c(harness: OrcaRuntimeHarness) -> RuntimeScenarioResult:
    """Scenario C: the per-phase iteration budget, exhausted by repeated FAILs.

    Extracted from run_runtime_scenarios() so its produced log rows can be asserted
    offline, the same way _scenario_g/h/i already are. That extraction is what makes
    the round_kind labelling below checkable: iteration 1 is the phase gate, and
    iterations 2-3 are correction rounds (their Worker mode says so), so each pair of
    dispatches is labelled for the round it actually belongs to rather than taking
    _log_attempt()'s phase_gate default.
    """
    run_id = harness.start_run("Step 4 Scenario C max iterations")
    attempts = []
    for iteration in range(1, 4):
        # One value, computed once and passed to BOTH sides of the round: the Worker
        # that does the work and the Reviewer that re-reviews it belong to the same
        # round, and labelling only one of them is how a round becomes unreadable.
        round_kind = "phase_gate" if iteration == 1 else "correction"
        worker, _ = harness.run_attempt(
            "worker", iteration, "complete" if iteration == 1 else "correction",
            phase=LIFECYCLE_SCENARIO_PHASE,
            resolutions={} if iteration == 1 else {"R1": "DISPUTED"},
            round_kind=round_kind,
        )
        reviewer, _ = harness.run_attempt(
            "reviewer",
            iteration,
            "fail",
            phase=LIFECYCLE_SCENARIO_PHASE,
            findings=("R1",),
            round_kind=round_kind,
        )
        attempts.extend((worker, reviewer))
    return harness.finish(
        RuntimeScenarioResult("C", run_id, "ESCALATED", 3, attempts)
    )


def _scenario_g(harness: OrcaRuntimeHarness) -> RuntimeScenarioResult:
    """Graph-first dependency promotion: no manual readiness override anywhere."""
    run_id = harness.start_run("Step 4 Scenario G graph-first dependency promotion")
    worker_spec = dispatch_context(
        "worker",
        1,
        "complete",
        phase=LIFECYCLE_SCENARIO_PHASE,
        run_id=run_id,
        quality_profile=harness.quality_profile,
        requested_phases=harness.requested_phases,
        risk=harness.risk,
        risk_source=harness.risk_source,
        agent_routing=harness.agent_routing,
    )[0]
    # OS-3 MIGRATION (site 1 of the seven-site verdict table): the one positive
    # Worker + dependent-Reviewer pair in this file now goes through the risk-aware
    # helper, so LOW creates no dependent Reviewer node at all.
    reviewer_spec = dispatch_context(
        "reviewer",
        1,
        "pass",
        phase=LIFECYCLE_SCENARIO_PHASE,
        run_id=run_id,
        quality_profile=harness.quality_profile,
        requested_phases=harness.requested_phases,
        risk=harness.risk,
        risk_source=harness.risk_source,
        agent_routing=harness.agent_routing,
    )[0]
    worker_task, reviewer_task = harness.create_phase_graph(worker_spec, reviewer_spec)
    if reviewer_task is None:
        raise OrcaRuntimeError(
            "scenario G requires a dependent Reviewer node; run it at medium/high risk"
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
        "worker",
        1,
        "complete",
        phase=LIFECYCLE_SCENARIO_PHASE,
        run_id=run_id,
        quality_profile=harness.quality_profile,
        requested_phases=harness.requested_phases,
        risk=harness.risk,
        risk_source=harness.risk_source,
        agent_routing=harness.agent_routing,
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
