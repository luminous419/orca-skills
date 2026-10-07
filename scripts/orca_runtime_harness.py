#!/usr/bin/env python3
"""Small real-Orca integration harness using deterministic fake agents."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from scripts import decision_contract
except ImportError:  # pragma: no cover - flat installed layout
    import decision_contract  # type: ignore[no-redef]

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
    from scripts.agent_profile import (
        MODEL_EVIDENCE_MISMATCH,
        MODEL_EVIDENCE_NONE,
        MODEL_EVIDENCE_REQUESTED,
        MODEL_EVIDENCE_STALE,
        MODEL_EVIDENCE_STATES,
        MODEL_EVIDENCE_UNVERIFIABLE,
        MODEL_EVIDENCE_VERIFIED,
        EVENT_AGENT_IDENTITY_BOUND,
        MODEL_SELECTION_VERIFIED_CAPABILITY,
        MODEL_TOKEN_PATTERN,
        REASON_WORKER_REVIEWER_MUST_DIFFER,
        driver_type_id,
        effective_identity_independent,
        model_selection_capabilities,
    )
    from scripts.deterministic_workflow import quiescence, turn_boundary
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
    from agent_profile import (
        MODEL_EVIDENCE_MISMATCH,
        MODEL_EVIDENCE_NONE,
        MODEL_EVIDENCE_REQUESTED,
        MODEL_EVIDENCE_STALE,
        MODEL_EVIDENCE_STATES,
        MODEL_EVIDENCE_UNVERIFIABLE,
        MODEL_EVIDENCE_VERIFIED,
        EVENT_AGENT_IDENTITY_BOUND,
        MODEL_SELECTION_VERIFIED_CAPABILITY,
        MODEL_TOKEN_PATTERN,
        REASON_WORKER_REVIEWER_MUST_DIFFER,
        driver_type_id,
        effective_identity_independent,
        model_selection_capabilities,
    )
    from deterministic_workflow import quiescence, turn_boundary
    from workflow_contract import load_workflow_output_contract


REPO_ROOT = Path(__file__).resolve().parents[1]
# OS-42 F-002. This module SHIPS: `release_manifest` installs it as
# `<skill>/tools/orca_runtime_harness.py` so `OrcaAdapter` -- the production Orca
# execution path -- has a complete dependency closure inside the installed package and
# imports no repository-only module. Two constants below were written for the repository
# layout and would silently mean the wrong directory once installed, so each is resolved
# from the layout this file actually finds itself in.
#
# `parents[1]` is the repository root for `<repo>/scripts/...` and the SKILL ROOT for
# `<skill>/tools/...`. A skill root is not a project: the project an installed
# Coordinator drives is the process's own working directory, which is also what `orca`
# resolves `--worktree current` against.
_INSTALLED_LAYOUT = Path(__file__).resolve().parent.name == "tools"
PROJECT_ROOT = Path.cwd() if _INSTALLED_LAYOUT else REPO_ROOT
# Resolved once, at import, and never again. dispatch_context used to call
# resolve_quality_profile() whenever its argument was omitted, which meant a profile
# edited while a Worker was running could hand that Worker's Reviewer a DIFFERENT
# quality model than the Worker was given -- the divergence ORIGINAL_REQUEST section
# 10 forbids. Run paths never reach this constant: OrcaRuntimeHarness resolves once
# per run in start_run() and threads its own resolution through every spec. This is
# only the answer for a standalone call with no run behind it, and it is a constant
# precisely so that even that path cannot re-read the file mid-sequence.
REPO_QUALITY_PROFILE = resolve_quality_profile(PROJECT_ROOT)
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
# The installed layout has SKILL.md at `<skill>/SKILL.md` and the repository layout at
# `<repo>/orca-worker-reviewer-orchestration/SKILL.md`. `decision_contract` already owns
# that resolution order (and the ORCA_SKILL_DIR override with it), so this reads it
# rather than growing a second, divergent, copy.
def resolve_skill_md() -> Path:
    """The orchestration SKILL.md this harness IS. Never guesses a second location."""
    for candidate in decision_contract.candidate_skill_paths():
        if candidate.is_file():
            return candidate
    # Nothing exists yet -- report the repository-layout path, which is what every
    # message about a missing SKILL.md said before this function.
    return REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"


SKILL_MD_PATH = resolve_skill_md()
REVIEWER_VERDICT_CONTRACT = load_workflow_output_contract(SKILL_MD_PATH)
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
# ---- OS-44. Delivery acknowledgement and turn quiescence -------------------------
# How many times one `check --ack` is attempted before the Coordinator fails closed.
# Bounded because an ack that never lands is not a condition that improves by being
# retried forever, and fail-closed because the alternative -- carrying on with the
# delivery unacknowledged -- is exactly the recorded defect: the next `check --wait`
# replays that batch and the newly armed waiter wakes on the previous phase's result.
ACK_MAX_ATTEMPTS = 3
# OS-44 (BUGFIX-I3-MAJOR-1). The two terminal outcomes of reconciling an acknowledgement
# a predecessor process left open. Both close the obligation; they differ only in what
# the runtime still held when the successor asked, and the audit says which.
ACK_RECONCILED_ACKNOWLEDGED = "reconciled_acknowledged"
ACK_RECONCILED_NOT_OUTSTANDING = "reconciled_not_outstanding"
# The third outcome, and the one the PR #31 review round 2 found missing: the successor
# could not establish EITHER of the two above. It is not terminal, it closes nothing,
# and the obligation stays open so every gate that reads it keeps failing closed.
ACK_RECONCILE_UNRESOLVED = "reconcile_unresolved"
# The ONLY error codes that authoritatively prove Orca no longer holds the delivery, so
# the only ones a reconciliation may treat as terminal. Read out of the shipped runtime
# rather than assumed: `acknowledgeRunDelivery` looks the delivery up by id and throws
# `stale_delivery` ("Delivery <id> does not belong to this Run") when no row for this Run
# carries it. Two neighbouring behaviours matter as much as the code itself and are why
# this set is one element long:
#   * a delivery Orca ALREADY consumed is still on file, so re-acking it RETURNS OK with
#     `duplicate: true`. The success path above therefore already covers the ordinary
#     "Orca consumed it" case; a failure is not needed to infer it and never proves it.
#   * `consumer_fenced` means this mailbox consumer was replaced -- it says nothing about
#     whether the delivery is outstanding, so it is deliberately NOT in this set.
# Every other failure -- transport, runtime unavailable, permission, invalid argument, a
# transient rejection, and any code a future runtime introduces -- leaves the obligation
# open. A generic failure is not evidence of consumption.
ACK_NOT_OUTSTANDING_ERROR_CODES = frozenset({"stale_delivery"})
# The delivery-ledger vocabulary, re-exported from the runtime-neutral contract so this
# module and the engine cannot spell the same state two ways.
DELIVERY_STATE_FIELD = quiescence.DELIVERY_STATE_FIELD
DELIVERY_STATE_PROCESSED = quiescence.DELIVERY_STATE_PROCESSED
DELIVERY_STATE_ACKNOWLEDGED = quiescence.DELIVERY_STATE_ACKNOWLEDGED
DELIVERY_STATE_ACK_FAILED = quiescence.DELIVERY_STATE_ACK_FAILED
DELIVERY_STATE_ACK_INTENT = quiescence.DELIVERY_STATE_ACK_INTENT
DELIVERY_STATE_ACK_RECONCILED = quiescence.DELIVERY_STATE_ACK_RECONCILED
DELIVERY_SETTLEMENT_CLAIMED_FIELD = quiescence.DELIVERY_SETTLEMENT_CLAIMED_FIELD
DELIVERY_SETTLED_FIELD = quiescence.DELIVERY_SETTLED_FIELD
DELIVERY_RECOVERED_FIELD = quiescence.DELIVERY_RECOVERED_FIELD
DELIVERY_ACK_INTENT_FIELD = quiescence.DELIVERY_ACK_INTENT_FIELD

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


def _receipt_error_code(payload: Any) -> str:
    """`payload["error"]["code"]` when it is a string, else "". No message text, no
    traceback text, no regex: a code is read where the runtime puts a code, or not at all.
    """
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        if isinstance(code, str):
            return code
    return ""


class OrcaCommandRefused(OrcaRuntimeError):
    """The runtime itself reported a failure IN A RECEIPT THIS PROCESS PARSED.

    A non-JSON, truncated, lost or transport-level failure is NOT this class: it stays a
    plain `OrcaRuntimeError`, because nothing was read back that says what happened. That
    distinction is the whole point: "the runtime refused" is CONFIRMED ABSENCE of the
    effect, and "the response could not be read" is an UNKNOWN outcome, and a durable
    record that conflates them is guessing.

    A SUBCLASS, so every existing `except OrcaRuntimeError` and every
    `assertRaises(OrcaRuntimeError)` binds exactly as before.
    """

    def __init__(
        self,
        message: str,
        *,
        command: tuple[str, ...],
        ok: Any,
        returncode: int,
        error_code: str,
        receipt_digest: str,
    ) -> None:
        super().__init__(message)
        self.command = tuple(command)   # the exact argv tuple, verbatim, no re-rendering
        self.ok = ok                    # payload["ok"] as the PRIMITIVE it was, or None
        self.returncode = returncode    # the process exit status
        self.error_code = error_code    # payload["error"]["code"] when present, else ""
        self.receipt_digest = receipt_digest  # sha256 of canonical JSON of the payload


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


#: What a diagnostic says in place of a value's own text when that text cannot be produced
#: at all. Named constants rather than inline literals because the regressions assert on
#: them: a diagnostic that says nothing is worse than one that says why it says nothing.
UNRENDERABLE_TEXT = "<unrenderable: __str__ and __repr__ both raised>"
UNRENDERABLE_TYPE_NAME = "<unrenderable type name>"
UNRENDERABLE_REPR = "<unrenderable: __repr__ raised>"


def safe_text(value: Any) -> str:
    """`str(value)` -- total for ordinary `Exception`; control flow propagates.

    OS-49 BUGFIX (review 5970292670, N-4). The superseded first line said "TOTAL. Never
    raises, whatever `value` does", and that was an unconditional claim the code has not
    made true since review F-002 narrowed the captures below from `BaseException` to
    `Exception`. Stated exactly, and this is the split every helper in this group shares:

        ordinary `Exception` from `__str__` / `__repr__`  -> handled, fallback text
        KeyboardInterrupt / SystemExit / GeneratorExit    -> PROPAGATE as themselves

    The split is deliberate and the behaviour is correct as it stands; N-4 is a wording
    correction and changes no capture. Do NOT re-widen anything here to `BaseException`
    in order to make the old sentence true -- that is the F-002 defect, reintroduced.

    OS-49 BUGFIX iteration 2 (review F-001). A diagnostic must not be able to replace the
    failure it is diagnosing. Every `except` handler in this module that renders what it
    caught is a normalization boundary, and on the model-selection path the caught object
    is NOT this repository's: it comes from `select_and_verify()` or from reading the
    `ModelEvidence` that call returned, which is arbitrary third-party code, and an
    exception class is free to define a `__str__` that raises. The eager `f"...{exc}..."`
    those handlers used therefore raised a NEW, unrelated exception from inside the
    handler that existed to stop exactly that, and the caller lost BOTH the normalized
    refusal and the diagnosis.

    Falls back `str` -> `repr` -> a fixed stand-in, because a fix that guarded only
    `str()` would still escape through a `repr()` fallback -- and `repr()` is the obvious
    fallback, so that second shape is the one a partial fix leaves open.

    `Exception` -- NOT `BaseException` -- is the correct width here, and iteration 2 had
    it wrong. OS-49 BUGFIX iteration 3 (review F-002). The superseded docstring argued
    that an interrupt raised by a hostile `__str__` "is not an operator stopping the run".
    That argument describes INTENT, and intent is precisely what this boundary cannot
    observe: `KeyboardInterrupt` IS a `BaseException`, so `except BaseException` consumed
    it regardless of where it came from. A REAL asynchronous interrupt -- the operator's
    Ctrl-C, delivered by the signal machinery while a slow `__str__` happened to be
    running -- was therefore swallowed and replaced by fallback text while the run carried
    on. Reproduced with a signal timer armed inside `__str__`:
        SWALLOWED <__str__ raised; repr: <fallback after swallowed operator interrupt>>

    `Exception` closes F-002 and keeps F-001 total in a SINGLE narrowing, because the
    split falls exactly on the control-flow boundary:
        KeyboardInterrupt / SystemExit / GeneratorExit  not `Exception` -> PROPAGATE
        MemoryError / RecursionError / RuntimeError          `Exception` -> still caught
    Every realistic hostile-`__str__`/`__repr__` failure is an `Exception`, so rendering
    stays total for all of them; every control-flow escape now leaves as itself.

    The one behaviour this deliberately moves: a hostile `__str__` that DELIBERATELY
    raises `KeyboardInterrupt` now propagates instead of being rendered. That is not a new
    hole, it is consistent with the surrounding design -- the post-selection handler
    stales the session and then re-raises non-`Exception` untouched under the R1 rule, so
    a driver that raises `KeyboardInterrupt` from `__str__` is treated identically to one
    that raises it from `select_and_verify()`, which the design explicitly says propagates
    as itself. Do not re-widen this to `BaseException`.
    """
    try:
        return str(value)
    except Exception:                                # noqa: BLE001 - total for Exception
        pass
    try:
        return f"<__str__ raised; repr: {value!r}>"
    except Exception:                                # noqa: BLE001 - total for Exception
        return UNRENDERABLE_TEXT


def safe_repr(value: Any) -> str:
    """`repr(value)` -- total for ordinary `Exception`; control flow propagates.

    OS-49 BUGFIX (review 5970292670, N-1). The `!r` sibling of `safe_text`, added for the
    one place that needs it: `_model_refusal()` renders the DRIVER'S evidence with `!r` in
    order to diagnose why a delivery was refused, and the driver owns every one of those
    field values. A `__repr__` that raises therefore let a DIAGNOSTIC replace the refusal
    it was diagnosing -- the F-001 shape, at the one render boundary F-001 did not reach
    because `!r` is not `str()`.

    Byte-identical to `f"{value!r}"` for every value that renders at all, so no existing
    refusal message, log row or test expectation moves.

    `Exception`, not `BaseException`, for the reason `safe_text` spells out: an interrupt
    raised inside `__repr__` is an operator decision and leaves as itself.
    """
    try:
        return repr(value)
    except Exception:                                # noqa: BLE001 - N-4 split, see above
        return UNRENDERABLE_REPR


def safe_type_name(value: Any) -> str:
    """`type(value).__name__` -- total for ordinary `Exception`; control flow propagates.

    The same reason as `safe_text`: a metaclass may define `__name__`, and `__name__` is
    not obliged to be a string.

    Catches `Exception`, not `BaseException`, for the reason spelled out in `safe_text`
    (review F-002): a metaclass `__name__` is arbitrary code, so an operator interrupt can
    land inside it, and an interrupt is not a rendering failure to be papered over.

    OS-49 BUGFIX (review 5970292670, N-4). The superseded word was "TOTAL", unqualified,
    which overstated a capture that is -- correctly -- `Exception`-wide only. Wording only;
    the capture is unchanged.
    """
    try:
        name = type(value).__name__
    except Exception:                                # noqa: BLE001 - total for Exception
        return UNRENDERABLE_TYPE_NAME
    return name if isinstance(name, str) else UNRENDERABLE_TYPE_NAME


def safe_exception_text(exc: BaseException) -> str:
    """`f"{type(exc).__name__}: {exc}"` -- total for ordinary `Exception`; control flow
    propagates.

    Byte-identical to that eager f-string for every exception that renders at all, so no
    existing refusal reason, message, log row or test expectation moves. It differs only
    where the eager form RAISED, which is the defect.

    OS-49 BUGFIX (review 5970292670, N-4). The superseded word was "TOTAL", unqualified.
    This helper is exactly as total as the two it composes and no more: an interrupt
    raised from inside `exc.__str__` or from a metaclass `__name__` leaves as itself.
    Wording only; no capture moves.
    """
    return f"{safe_type_name(exc)}: {safe_text(exc)}"


def _writer_label(writer: Any) -> str:
    """The name a `_safe_log` row carries for its writer -- as total as the error text:
    ordinary `Exception` is handled, control-flow exceptions propagate.

    `getattr(writer, "__name__", writer)` is itself a render of a caller-supplied object
    on the fallback leg, and `__name__` may be a raising property, so both legs are
    guarded.

    `Exception`, not `BaseException`, for the reason spelled out in `safe_text` (review
    F-002). This is the fourth render boundary of the same shape; F-001 was a fix applied
    at one site while a sibling kept the defect alive, so the narrowing is applied to
    every one of them at once rather than only to the lines the finding cited.

    OS-49 BUGFIX (review 5970292670, N-4). "total, like the error text" was the same
    unconditional claim as `safe_text`'s, inherited by reference; it now carries the same
    qualification. Wording only; both legs still catch `Exception`.
    """
    try:
        name = writer.__name__
    except Exception:                                # noqa: BLE001 - total for Exception
        name = writer
    return safe_text(name)


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


# ---- OS-49: the model-selection seam's two closed vocabularies ------------------------
# Both are snake_case `<axis>_<verdict>`, the naming shape
# `standalone_lifecycle.CAPABILITY_FAILURE_REASONS` and `standalone_preflight.REASONS`
# already use.  They are ORCHESTRATION-ONLY, because only this runtime has a
# pre-delivery barrier; the SCREAMING_SNAKE policy codes live in `agent_profile` and are
# imported above rather than restated here, so the two layers cannot drift.

#: How a model selection may be REQUESTED.  CLOSED, and deliberately ONE member.
#: `launch_argv` is NOT a member: a model-pinned wrapper's argv was composed before this
#: session existed, so it is not a request attributable to THIS attempt -- it is evidence
#: of what was requested of the operating system, never of what a provider resolved.  An
#: interactive `/model` member cannot be added without the syntax and acknowledgement
#: semantics this repository has not observed and refuses to invent.  So this tuple is a
#: second, independent mechanism keeping the real Claude runtime fail-closed: no real
#: adapter can name a member, and a `request_method` outside the set is
#: `model_selection_unsupported`.  OS-14 adding a member is the visible, reviewable act
#: that lifts the refusal.
MODEL_SELECTION_REQUEST_METHODS = ("driver_select_and_verify",)

#: How a resolved model may be OBSERVED.  CLOSED, and -- like the request methods above --
#: deliberately ONE member, naming the only locator any driver in this release actually
#: reads (`fake_adapter.InProcessModelDriver.OBSERVATION_METHOD`).
#:
#: OS-49 BUGFIX (review 5970292670, N-1a).  `observation_method` was the one attested
#: field of `ModelEvidence` the barrier never validated: declared `str` and only ever
#: RENDERED, so an arbitrary object passed the barrier and was stored as authority, then
#: evaluated later from the settled-dispatch logging funnel.  It is leg 2's counterpart to
#: `request_method` and now has leg 2's counterpart of that field's closed-set rule.
#:
#: The contract is TYPE AND VALUE, in that order, and the type test is `type(...) is str`
#: rather than `isinstance`: a `str` SUBCLASS may override `__eq__`, so an `isinstance`
#: gate would hand the membership test below an object that can still raise -- which is
#: the defect, one layer down.  A value outside the set is never coerced into one with
#: `str()` or `safe_text()`; coercion is what turns an arbitrary object into apparently
#: valid evidence, and this barrier's whole job is to refuse it instead.
#:
#: OS-49 BUGFIX (final review F-013).  The contract is unchanged; its TYPE half MOVED.  It
#: was the only attested field with such a check, and a check at its point of use runs
#: after earlier legs have already touched other unvalidated values, so all fifteen type
#: checks now sit in one hoisted gate immediately after the attested snapshot.  This
#: tuple's membership test -- the VALUE half -- still runs at leg 2, under the same reason
#: code and with the same diagnostic.
#:
#: A real `/model` locator (OS-14) adding a member is the visible, reviewable act that
#: admits it, exactly as it is for `MODEL_SELECTION_REQUEST_METHODS`.
MODEL_SELECTION_OBSERVATION_METHODS = ("in_process_session_state",)

#: In LIFECYCLE ORDER, so the tuple itself documents that the request precedes the
#: observation.  Every member is a refusal BEFORE delivery; none is repaired by retrying,
#: by falling back to a weaker observation, or by downgrading to a warning.
MODEL_SELECTION_UNSUPPORTED = "model_selection_unsupported"
MODEL_SELECTION_REQUEST_ABSENT = "model_selection_request_absent"
MODEL_SELECTION_REQUEST_STALE = "model_selection_request_stale"
MODEL_SELECTION_UNVERIFIED = "model_selection_unverified"
MODEL_SELECTION_MISMATCH = "model_selection_mismatch"
MODEL_SELECTION_AMBIGUOUS = "model_selection_ambiguous"
MODEL_SELECTION_PAIR_UNADMITTED = "model_selection_pair_unadmitted"
MODEL_SELECTION_FAILURE_REASONS = (
    # ---- the REQUEST leg ----
    MODEL_SELECTION_UNSUPPORTED,     # a selection cannot be requested at all: no driver,
                                     # or no supported request method
    MODEL_SELECTION_REQUEST_ABSENT,  # a result with no attested request -- the
                                     # pre-existing / default model-state case
    MODEL_SELECTION_REQUEST_STALE,   # a request attested for another attempt or key, or
                                     # ordinals not drawn from this ticket, not exactly
                                     # two, or not in order
    # ---- the OBSERVATION leg ----
    MODEL_SELECTION_UNVERIFIED,      # requested, never positively observed
    MODEL_SELECTION_MISMATCH,        # requested != resolved
    MODEL_SELECTION_AMBIGUOUS,       # two contradictory resolved values for one identity
    # ---- the PAIR leg: positive Worker/Reviewer independence, before any delivery ----
    MODEL_SELECTION_PAIR_UNADMITTED, # a same-command pair whose COUNTERPART has no
                                     # verified model evidence yet, so independence is
                                     # not positively established and NOTHING may be
                                     # delivered for either role
)

#: The shared second clause of a TYPE-GATE refusal, for the thirteen attested fields that
#: have no leg-specific sentence of their own (OS-49 BUGFIX, final review F-013).  Spelled
#: once so fifteen refusals cannot drift apart, and worded about what the harness needs
#: rather than about the driver's intent: a value this barrier must compare, store and
#: render has to BE a value, and an object that defines those operations itself is still
#: driver code at every later read.
ATTESTED_PRIMITIVE_NOTE = (
    "an attested model-evidence field must be a value this harness can compare, store "
    "and render, not an object whose behaviour the driver still controls"
)

#: Per-field reason code for a failed type gate.  Only the two fields whose malformed-type
#: contract is already LOCKED by tests appear here; every other field falls back to
#: `model_selection_unverified`, because an attested field that is not a primitive is not
#: evidence of anything and `unverified` is the vocabulary's name for that.
#:
#:   * `observation_method` -> `model_selection_unsupported`.  N-1a's contract: leg 2
#:     attests WHICH declared locator produced the resolved value, so a value that is not
#:     a name does not name a locator this repository implements.
#:   * `resolved_model` -> `model_selection_unverified`.  The B3 malformed-type contract,
#:     which enumerates eight non-string shapes and requires every one of them to leave
#:     the barrier inside the closed vocabulary under this member.
ATTESTED_FIELD_TYPE_REFUSALS = {
    "observation_method": MODEL_SELECTION_UNSUPPORTED,
    "resolved_model": MODEL_SELECTION_UNVERIFIED,
}

#: The reuse gate's model condition names, beside `agent_command_mismatch` and
#: `observation_not_for_this_dispatch`.
MODEL_IDENTITY_MISMATCH = "model_identity_mismatch"
MODEL_IDENTITY_UNVERIFIED = "model_identity_unverified"
MODEL_IDENTITY_STALE = "model_identity_stale"
MODEL_CAPABILITY_UNSUPPORTED = "model_capability_unsupported"
MODEL_IDENTITY_FAILURE_REASONS = (
    MODEL_IDENTITY_MISMATCH,
    MODEL_IDENTITY_UNVERIFIED,
    MODEL_IDENTITY_STALE,
    MODEL_CAPABILITY_UNSUPPORTED,
)


#: "this key was not present", as distinct from a recorded empty string. Used by the
#: model-evidence snapshot (review N1) so a rollback can tell `pop()` from `set to ""` and
#: restore a row to exactly the shape it had, rather than to a plausible one.
_ABSENT = object()


@dataclass(frozen=True)
class ModelSelectionTicket:
    """ONE model-selection attempt.  Minted by the BARRIER -- never by a caller, never by
    a driver -- and revoked the moment the driver's single call returns.

    `token` and `stamp` are the two things that make the request leg unforgeable and
    non-carryable, and they are deliberately different mechanisms:

    * `token` is the attempt's IDENTITY: the barrier's own six-part key plus a run-scoped
      ordinal the harness issues exactly once and never reissues.  No previous attempt, no
      other session, no other `(role, phase)` and no pre-existing model state can hold it,
      because it did not exist until this barrier ran.
    * `stamp` is the attempt's ORDERING: a bound closure over the harness's private
      monotone counter.  A driver cannot obtain a valid ordinal without CALLING it, and the
      barrier knows the counter's value before and after the call -- so a fabricated
      ordinal, a reused one, a missing one and an extra one are all detectable
      arithmetically, by the harness, with no provider knowledge and no trust in the
      driver's own account of what it did.

    Why ordinals and not a timestamp: a clock reading a driver prints itself is not
    ordering evidence, and this package already refuses self-reported evidence of that
    shape.  A counter the driver does not OWN is different in kind -- the only way to get
    the next ordinal is to ask the harness, and the harness counts how often it was asked.
    """

    run_id: str
    task_id: str
    terminal: str
    role: str
    phase: str
    attempt: int
    command: str
    requested_model: str
    #: f"{run}:{task}:{terminal}:{role}:{phase}:{attempt}:{ordinal}"
    token: str
    #: Single-use window.  Raises once the barrier revokes the ticket.
    stamp: Callable[[], int]


@dataclass(frozen=True)
class ModelEvidence:
    """One model-selection ATTEMPT: what was requested, through what, when -- and what was
    THEN observed.

    TWO LEGS, each separately attested, because a verdict about only the second one is
    satisfiable by reading a model state nothing in this attempt asked for.  Whatever model
    a fresh session happens to be on before anything requested a change is a STATE, not the
    RESULT of a selection.

    Defined HERE, beside `ReuseObservation`, rather than in `deterministic_workflow`: this
    module is the runtime and takes no engine-package import.  The SCREAMING_SNAKE policy
    vocabulary and the six model-evidence states are imported from `agent_profile`, which
    owns them, so there is exactly one spelling of each.
    """

    state: str = ""                    # one of agent_profile.MODEL_EVIDENCE_STATES
    requested_model: str = ""
    resolved_model: str = ""
    # ---- leg 1: the REQUEST, attested -------------------------------------------------
    selection_token: str = ""          # MUST equal the ticket's token, exactly
    request_method: str = ""           # MUST be in MODEL_SELECTION_REQUEST_METHODS
    request_stamp: int = 0             # MUST be the ticket's FIRST drawn ordinal
    # ---- leg 2: the OBSERVATION -------------------------------------------------------
    observation_method: str = ""       # MUST be in MODEL_SELECTION_OBSERVATION_METHODS:
                                       # which declared locator produced the resolved value
    observe_stamp: int = 0             # MUST be the SECOND drawn ordinal, > request_stamp
    capability: str = ""               # MUST be MODEL_SELECTION_VERIFIED_CAPABILITY
    observed_at_run: str = ""
    observed_at_task: str = ""
    observed_at_terminal: str = ""
    observed_at_role: str = ""
    observed_at_phase: str = ""
    observed_at_attempt: int = 0

    @property
    def request_evidence(self) -> str:
        """The one-cell rendering the ledger and the durable provenance row carry."""
        return f"{self.selection_token}:{self.request_stamp}->{self.observe_stamp}"


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
    repair_instruction: Any | None = None,
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
        role=current_role, phase=phase, run_id=run_id, gate_iteration=iteration
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
            drill_down=(str(PROJECT_ROOT),),
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
    # OS-42: the generated decision-gate contract, and on a repair dispatch the defect
    # that caused it. This is the ONLY place a real agent's prompt text is built, which
    # is why the generator is called here rather than anywhere further out: the OS-40
    # engine reaches it through `run_existing_task`, which re-renders `base_spec` through
    # this function, and its own docstring records that this re-render "is the text that
    # actually reaches worker-start".
    projection = decision_contract.contract_projection(decision_contract.resolve_policy())
    contract_block = decision_contract.render_worker_contract(
        projection, run_id=run_id, phase=phase, iteration=iteration, role=current_role,
    )
    repair_block = (
        None if repair_instruction is None
        else decision_contract.render_repair_instruction(repair_instruction, projection)
    )
    return (
        render_task_spec(
            base,
            boundary,
            reviewer_context,
            quality_gate,
            risk_context,
            routing_context,
            contract_block,
            repair_block,
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
        quality_profile_root: Path = PROJECT_ROOT,
        risk: str = "high",
        risk_source: str = "default",
        agent_routing: Any | None = None,
        model_driver: Any | None = None,
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
        # ---- OS-49. The ONLY thing that can make a declared model routable here.
        # `None` is the default and means NO CAPABILITY: with no driver a selection
        # cannot even be REQUESTED, so a declared model is refused before delivery
        # with `model_selection_unsupported`. A driver must implement ONE method,
        # `select_and_verify(ticket) -> ModelEvidence`, and must attest BOTH ordered
        # legs of the lifecycle for the attempt the ticket names. Declaring no model
        # anywhere means the driver is never consulted and this harness behaves
        # byte-identically to before OS-49.
        self.model_driver = model_driver
        # (phase, role) -> the ModelEvidence the barrier ACCEPTED. Written only on a
        # successful barrier, never on a refusal, so it can never record a value that
        # did not earn a delivery. This is what makes the resolved-value independence
        # re-check and the non-drift refusal possible: a Reviewer's barrier reads its
        # Worker counterpart's accepted identity, and a later round that resolves to a
        # DIFFERENT model for the same (phase, role) is refused rather than logged.
        self._model_identity: dict[tuple[str, str], ModelEvidence] = {}
        # The harness-private monotone ordinal counter. A driver cannot advance it
        # except by calling the bound `stamp()` of a ticket this harness minted, and
        # the barrier reads it before and after the driver's one call -- which is the
        # whole of the ordering proof, and it is integer arithmetic over values this
        # harness itself issued.
        self._model_selection_seq: int = 0
        # Tokens whose `stamp()` window is still open. A ticket is revoked in a
        # `finally:`, so a raising driver cannot leave a live stamp behind, and a
        # driver that keeps the ticket and stamps later gets an error rather than a
        # usable ordinal from the NEXT attempt's window.
        self._model_selection_open_tokens: set[str] = set()
        # terminal handle -> the accepted evidence awaiting rebinding to a dispatch id.
        # The Dispatch does not exist yet when the barrier runs on rung 3 (the very call
        # that delivers creates it), so the evidence is keyed on the handle here and
        # rebound by _attach_terminal() the moment the id is known.
        self._model_pending_evidence: dict[str, ModelEvidence] = {}
        # OS-49 BUGFIX (review M2/M5). terminal handle -> (routing_role, phase, evidence)
        # for the LAST evidence this run accepted on that PHYSICAL SESSION. The two maps
        # above are keyed on (phase, role) and on a pending rebinding; neither can answer
        # a question about the SESSION, and both of the defects this map closes were
        # questions about the session:
        #
        #   M2 cross-phase resolved-model drift -- one terminal reused from IMPLEMENTATION
        #       into TEST with the SAME requested alias resolving to a DIFFERENT model.
        #       The (phase, role) key differs, so leg (i) never compared them and equal
        #       request aliases were accepted as proof of an unchanged agent.
        #   M5 one session verified as BOTH roles -- `ModelEvidence.observed_at_terminal`
        #       carried the discriminator all along and nothing read it, so a same-command
        #       pair could be "admitted" by one physical terminal playing both parts,
        #       which is exactly what `Worker session != Reviewer session` forbids.
        #
        # Run-scoped like the two above and cleared by finish() for the same reason: every
        # record in it was accepted against ONE run's six-part attempt key.
        self._model_session_identity: dict[str, tuple[str, str, ModelEvidence]] = {}
        # ---- OS-49 BUGFIX (review B2). The IDENTITY HISTORY, which is a different thing
        # from the three maps above and needs a different LIFETIME.
        #
        # The three maps above hold CURRENT AUTHORITATIVE EVIDENCE: what pair admission
        # and reuse condition 9 are allowed to act on. Authority is REVOCABLE -- once a
        # selection has executed and then failed validation, nothing in them still
        # describes the physical session, so `_stale_model_evidence()` drops them.
        #
        # These two hold IDENTITY HISTORY: the bare fact that, at some point in THIS run,
        # a `(phase, role)` slot and a physical session were positively observed resolving
        # to a particular model. That fact does not stop being true when the authority
        # derived from it is revoked, and B2 is what happened when the two were conflated:
        #
        #     session S:  alias-X -> model-A   accepted
        #     same S:     alias-X -> model-B   refused as drift, which STALED S
        #     retry on S: alias-X -> model-B   ACCEPTED, because the staling had deleted
        #                                      the very baseline that knew S was model-A
        #
        # So a refused drift attempt was the way to LAUNDER a drifting session into an
        # accepted one -- two refusals in a row would have been fail-closed, and instead
        # the first refusal cleared the ground for the second attempt to pass. History is
        # therefore APPEND-ONLY for the life of the run and survives staling, while
        # authority stays revocable. Drift legs (i) and (k) read HISTORY; pair admission,
        # reuse and the provenance rows read AUTHORITY. Neither can do the other's job.
        #
        # Recovery is unaffected and still works, because recovery means resolving BACK to
        # the baseline: S re-selected onto model-A matches its history and is accepted, and
        # its authority is re-established by that fresh positive observation. What is now
        # refused is re-badging S as a DIFFERENT model, for which the remedy is a new
        # session -- which is available and cheap -- not an erased history.
        #
        # OS-49 BUGFIX (review 5970292670, N-3). "A new session" is the remedy for a
        # DRIFTED SESSION, and it is not a way to change a ROLE's model. The replacement
        # must still resolve to the role's established baseline: leg (i) is keyed on
        # (phase, routing role) and reads `_model_role_history`, which moving to a fresh
        # terminal does not touch, so a replacement resolving to a different model is
        # refused on the new session for the rest of the run, exactly as it was on the
        # old one. A role's resolved model changes in a NEW RUN, never mid-run.
        #
        # Only the resolved model is kept, not the evidence: history answers "what did
        # this slot/session resolve to", and holding the whole record would invite some
        # future reader to treat a historical record as authority, which is the conflation
        # this split exists to prevent.
        #
        # RUN-SCOPED, which is what keeps B1's reset sufficient: cleared in `start_run()`
        # beside the three maps above and in `finish()`, so no run can read another run's
        # history and a declared model may legitimately change between runs.
        self._model_role_history: dict[tuple[str, str], str] = {}
        self._model_session_history: dict[str, tuple[str, str, str]] = {}
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
        # ---- OS-44. delivery_id -> {state, replays, task_id, dispatch_id, message_id}.
        # The Coordinator's own message-loop ledger, and a different object from
        # self._ledger above: that one answers "did I already finalize this Dispatch?",
        # this one answers "did I already consume -- and acknowledge -- this Delivery?".
        # The recorded `run_c2166e75bb02` failure needed both and had only the first,
        # which is why its completed-dispatch ledger correctly blocked the duplicate
        # settlement while the stale delivery still consumed a wait cycle.
        self._deliveries: dict[str, dict[str, Any]] = {}
        # OS-44. The run whose predecessor ledger this PROCESS has already recovered.
        # Empty means "this process has not yet folded the run's audit forward", and
        # _check() refuses to arm a waiter until it has -- which is what makes restart
        # recovery a property of the production wait path rather than of a helper a
        # caller has to remember to invoke.
        self._deliveries_restored_for: str = ""
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
        self._decision_policy = decision_policy.load_decision_policy(SKILL_MD_PATH)
        # The (run, phase, iteration) of the round this PROCESS last settled and
        # recorded in the ledger, or None. A3's binding expectation, held in memory
        # and never read back off the ledger it validates. It is deliberately
        # process-local: OS-31 owns cross-session resume, so a fresh Coordinator
        # meeting a non-trivial ledger fails closed rather than guessing (L7).
        self._last_settled: tuple[str, str, int] | None = None
        # OS-42 F-002. The round whose settled boundary produced an INPUT DEFECT rather
        # than a published record, or None. `_last_settled` alone cannot answer that: it
        # is advanced identically whether the record was published or refused, which is
        # exactly what makes the next ordinary B1 refuse as UNBOUND. A bounded
        # validation repair is the ONE dispatch that is allowed to meet that state,
        # because it re-asks the SAME boundary rather than moving past it. Cleared the
        # moment a record is published for the round, so a repair can never be used to
        # skip a boundary that really did settle.
        self._last_input_defect: tuple[str, str, int] | None = None
        # OS-42 round 3. The decision this process already reached for each settled
        # DISPATCH. `_log_attempt` is the single funnel every settled dispatch passes,
        # and it runs on the REPLAY path too -- `settle_attempt`'s finalize-once gate
        # returns the recorded attempt, and the caller logs it again. Judging that
        # attempt a second time is wrong in both directions: before this cache the
        # ingress relabelled the first settlement's record with the second call's phase
        # and published a mislabelled DUPLICATE row, and after the round-3 identity gate
        # it would instead refuse the replay and poison a run that had settled cleanly.
        # A replay is not a result: it answers with what the dispatch already decided,
        # publishes nothing, and advances no binding.
        self._settled_dispatch_decisions: dict[str, tuple[str, str]] = {}
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
            cwd=PROJECT_ROOT,
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
            # TYPED, because a parsed receipt that says `ok:false` is the runtime's own
            # report that the effect did NOT happen -- the only evidence a durable record
            # may read as confirmed absence. The `json.JSONDecodeError` branch above keeps
            # raising the BASE class: a response that was never parsed says nothing.
            raise OrcaCommandRefused(
                f"Orca command failed ({' '.join(args)}): {payload.get('error')}",
                command=tuple(args),
                ok=payload.get("ok"),
                returncode=returncode,
                error_code=_receipt_error_code(payload),
                receipt_digest=hashlib.sha256(
                    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
                        "utf-8"
                    )
                ).hexdigest(),
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
        requested_model: str = "",
        model_state: str = "",
    ) -> dict[str, Any]:
        """Create the ledger row for a terminal at creation/adoption time.

        role and origin are the only axis (c2) evidence that exists, and the runtime
        keeps neither, so they are recorded here or lost forever.

        OS-49 adds six model fields under the SAME "never overwrite a recorded value
        blank" rule the existing evidence follows. Only `requested_model` and
        `model_state` can be supplied at creation -- at creation nothing has been
        requested and nothing resolved, so the other four are written later, by the
        barrier and by _attach_terminal(), and are deliberately not parameters.
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
                # ---- OS-49 model identity evidence -------------------------
                # What the materialized routing DECLARED for this role.
                "requested_model": requested_model,
                # Written by GATE B only, never at creation: a resolved model is the
                # result of a selection this session was asked to make, and nothing
                # has asked yet.
                "resolved_model": "",
                "model_state": model_state or MODEL_EVIDENCE_NONE,
                # The REQUEST leg, written by _attach_terminal() from the evidence the
                # barrier ACCEPTED. A `verified` row with these blank is a
                # contradiction, and reuse condition 9 refuses it as such.
                "model_request_method": "",
                "model_request_evidence": "",
                "model_observed_at_dispatch": "",
            }
        else:  # ownership transfer, never a role promotion (reuse chain)
            row["owner_dispatch_id"] = owner_dispatch_id or row["owner_dispatch_id"]
            if created_by:
                row["created_by"] = created_by
            if agent_command:                 # never overwrite a recorded value blank
                row["agent_command"] = agent_command
            if requested_model:               # same rule, same reason
                row["requested_model"] = requested_model
            if model_state:
                row["model_state"] = model_state
            # A row created before OS-49 existed in this process (a test that built one
            # by hand, a recovered shape) is brought up to the full key set WITHOUT
            # overwriting anything it already carries.
            for key, default in (
                ("requested_model", ""),
                ("resolved_model", ""),
                ("model_state", MODEL_EVIDENCE_NONE),
                ("model_request_method", ""),
                ("model_request_evidence", ""),
                ("model_observed_at_dispatch", ""),
            ):
                row.setdefault(key, default)
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

    def adopt_prepared_terminal(
        self, handle: str, role: str, *, phase: str = ""
    ) -> dict[str, Any]:
        """Register a DIGEST-PROVED prepared session in THIS process's ledger.

        The successor-process counterpart of what `create_fake_terminal` does for a
        session this process created, and the same pattern `OrcaAdapter.account_dispatch`
        already uses to re-seed a recovered handle from the journal before consuming
        harness ledger state.

        Provenance is the SAME derivation `create_fake_terminal` records, read from the
        LIVE routing rather than from the durable record -- so an adoption cannot import
        a predecessor's declaration.

        RESTORES NO VERIFICATION AUTHORITY, and cannot: `register_terminal` takes no
        `resolved_model` parameter at all and writes that cell "" on a new row, and the
        only states supplied here are `requested` / `none`. `_model_identity`,
        `_model_session_identity`, `_model_role_history` and `_model_session_history` are
        NOT touched -- a positive re-verification through the live driver is the only way
        this process gains authority for the session.

        IDEMPOTENT, and provenance-preserving on a SAME-PROCESS re-entry: a handle this
        process created is already in `self._terminals`, and `register_terminal`'s
        existing-row branch transfers ownership without touching `role` or `origin`, so a
        self-created session keeps `origin == "self_created"` and only an unseen handle is
        recorded as `adopted`.

        Issues NO `orca` command: it is a ledger write over a handle a parsed listing
        already proved (`listing_verified`), never a probe.
        """
        requested_model = self.resolved_agent_model(role, phase)
        return self.register_terminal(
            handle,
            role="active_worker",
            origin="adopted",
            intended_role=(
                "phase_reviewer" if role.endswith("reviewer") else "phase_worker"
            ),
            agent_command=self.resolved_agent_command(role, phase),
            requested_model=requested_model,
            model_state=(
                MODEL_EVIDENCE_REQUESTED if requested_model else MODEL_EVIDENCE_NONE
            ),
        )

    def _rebind_model_evidence(self, handle: str, dispatch_id: str) -> None:
        """OS-49. Rebind the barrier's accepted evidence to the Dispatch id.

        The key the barrier verifies against is
        `(run, task, handle, role, phase, attempt)` and NOT `dispatch_id`, because on
        rung 3 the Dispatch is created by the very call that delivers -- so the id does
        not exist yet when the evidence has to be judged. The moment it does exist, the
        evidence is rebound here, together with its REQUEST leg: those three cells are
        what reuse condition 9's staleness and contradiction rows read, and what the
        durable provenance row carries. The ticket object itself is already revoked and
        is never stored.
        """
        evidence = self._model_pending_evidence.pop(handle, None)
        row = self._terminals.get(handle)
        if evidence is None or row is None:
            return
        row["resolved_model"] = evidence.resolved_model
        row["model_state"] = evidence.state
        row["model_request_method"] = evidence.request_method
        row["model_request_evidence"] = evidence.request_evidence
        row["model_observed_at_dispatch"] = dispatch_id

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
            self._rebind_model_evidence(handle, dispatch_id)
            return row
        row = self.register_terminal(
            handle,
            role="external_or_adopted",
            origin="adopted",
            owner_dispatch_id=dispatch_id,
            created_by=created_by,
        )
        self._rebind_model_evidence(handle, dispatch_id)
        return row

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

    def list_terminals(
        self, *, worktree: str = "current", limit: int | None = None
    ) -> tuple[dict[str, Any], ...]:
        """`orca terminal list --worktree <selector> --json`, verbatim. Read-only.

        Grammar read from `orca terminal list --help`; the response shape was executed and
        observed against a live runtime, and every element carries `handle` and `title`.
        OS-31 consumes exactly three fields -- `handle`, `title` and `orphaned` (the last as
        reporting evidence only) -- so response drift outside those three cannot affect it.
        Issues no mutation, so it is safe to repeat.

        `worktree` is defaulted only so the contract test's public-method sweep can bind it;
        OS-31 always passes the stable `id:<repo-id>::<path>` selector it journalled, never
        the `current`/`active` alias.
        """
        args = ["terminal", "list", "--worktree", worktree]
        if limit is not None:
            args.extend(["--limit", str(limit)])
        payload = self.call(*args)
        terminals = (payload.get("result") or {}).get("terminals")
        if not isinstance(terminals, list):
            raise OrcaRuntimeError("terminal listing has an unexpected shape")
        return tuple(dict(item) for item in terminals if isinstance(item, dict))

    def resolve_worktree(self, selector: str = "current") -> dict[str, Any] | None:
        """`orca worktree show --worktree <selector> --json`, or None when it does not resolve.

        A listing under an UNRESOLVABLE selector returns `ok: true` with an empty array --
        indistinguishable, on its own, from a real worktree holding no terminals. So an
        "absent" verdict must be PROVED with this guard rather than inferred from emptiness.
        """
        payload = self.call(
            "worktree", "show", "--worktree", selector, allow_error=True
        )
        if not payload.get("ok"):
            return None
        worktree = (payload.get("result") or {}).get("worktree")
        return dict(worktree) if isinstance(worktree, dict) else None

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
        requested_model: str = "",
        dispatch_id: str = "",
        observation: "ReuseObservation | None" = None,
    ) -> tuple[bool, tuple[str, ...]]:
        """The NINE-condition reuse gate. Returns (eligible, failure names).

        OS-49 APPENDED condition 9 (`compatible_model_identity`). It did not replace or
        relax any of the eight that were here, and it can only ever REFUSE a reuse that
        would otherwise have been allowed -- so no ownership, finality or provenance
        protection is weakened, and nothing that was refused before becomes allowed.
        Composition, exactly: existing eligibility AND compatible command AND positively
        compatible model identity.

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

        # ---- 9. compatible model identity (OS-49) -----------------------------
        # Evaluated the same never-short-circuit way as the eight above, so a negative
        # test binds to exactly one name. Two keys, two checks, neither substituting for
        # the other: THIS condition judges the PREVIOUS dispatch's recorded identity
        # against the next request, while the pre-delivery barrier judges THIS dispatch's
        # resolved model before it delivers.
        reasons.extend(
            self._model_identity_reuse_reasons(
                row, requested_model=requested_model, dispatch_id=dispatch_id
            )
        )

        if reasons:
            # De-duplicated: lifecycle_recovery_state() answers
            # `previous_dispatch_not_finalized` for an absent settlement row, which is
            # the same fact condition 4 names. One fact, one name.
            return False, tuple(sorted(set(reasons)))
        return True, ()

    def _model_identity_reuse_reasons(
        self, row: dict[str, Any], *, requested_model: str, dispatch_id: str
    ) -> tuple[str, ...]:
        """Reuse condition 9, as a total function over the recorded row.

        Row 1 of the truth table is the compatibility guarantee and it is the FIRST
        branch: a chain with no model anywhere -- the previous dispatch recorded none and
        the next dispatch requests none -- produces exactly the pre-OS-49 decision, with
        no new name and nothing read. Everything after that branch requires a model to be
        involved on at least one side.
        """
        recorded_state = str(row.get("model_state") or MODEL_EVIDENCE_NONE)
        recorded_requested = str(row.get("requested_model") or "")
        recorded_resolved = str(row.get("resolved_model") or "")
        recorded_method = str(row.get("model_request_method") or "")
        recorded_request_evidence = str(row.get("model_request_evidence") or "")
        recorded_dispatch = str(row.get("model_observed_at_dispatch") or "")
        no_model_recorded = (
            recorded_state == MODEL_EVIDENCE_NONE
            and not recorded_requested
            and not recorded_resolved
        )
        if no_model_recorded and not requested_model:
            return ()                       # row 1: today's decision, byte-identical

        reasons: list[str] = []
        # Row 9. No driver means THIS dispatch could not request a selection either, so
        # there is no way to re-establish the identity the reuse would carry forward.
        # OS-49 BUGFIX (review M3/M6): asked through the ONE capability derivation the two
        # gates read, so a driver object that exists but cannot be called is `unsupported`
        # here exactly as it is at the barrier, rather than passing this condition and
        # then leaking a raw AttributeError at delivery time.
        if MODEL_SELECTION_VERIFIED_CAPABILITY not in model_selection_capabilities(
            self.model_driver
        ):
            reasons.append(MODEL_CAPABILITY_UNSUPPORTED)
        # Row 8. An internally contradictory row is never a basis for reuse. A `verified`
        # model with no record that anything REQUESTED it is exactly as contradictory as a
        # `verified` model with no resolved value.
        contradictory = (
            (recorded_state == MODEL_EVIDENCE_VERIFIED and not recorded_resolved)
            or (
                recorded_state == MODEL_EVIDENCE_VERIFIED
                and (not recorded_method or not recorded_request_evidence)
            )
            or (recorded_resolved and recorded_state != MODEL_EVIDENCE_VERIFIED)
            or (
                recorded_method
                and recorded_method not in MODEL_SELECTION_REQUEST_METHODS
            )
        )
        if contradictory:
            reasons.append(MODEL_IDENTITY_STALE)
        # Row 7. Evidence that was not observed for the dispatch being handed over is
        # stale by construction. A DIFFERENT key from the barrier's: this one is about the
        # PREVIOUS dispatch.
        #
        # Gated on the row actually CLAIMING evidence. When the previous dispatch recorded
        # none at all, "its evidence is not for this dispatch" is vacuously true and would
        # emit a second name for one fact -- and the fact is the other one: no verified
        # model was ever recorded, i.e. `model_identity_unverified`. One fact, one name, as
        # the eight pre-existing conditions already require.
        elif (
            (recorded_state != MODEL_EVIDENCE_NONE or recorded_resolved)
            and recorded_dispatch != dispatch_id
        ):
            reasons.append(MODEL_IDENTITY_STALE)
        # Rows 5 and 6. `unverified` is not `pass`: a previous identity that was never
        # positively resolved proves nothing about the session's current model.
        if recorded_state != MODEL_EVIDENCE_VERIFIED:
            reasons.append(MODEL_IDENTITY_UNVERIFIED)
        # Rows 3 and 4. A changed model is a changed agent, and declared -> undeclared is
        # a change like any other.
        elif recorded_requested != requested_model:
            reasons.append(MODEL_IDENTITY_MISMATCH)
        return tuple(reasons)

    def terminal_for_next_dispatch(
        self,
        handle: str = "",
        *,
        role: str = "",
        agent_command: str = "",
        requested_model: str = "",
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
            requested_model=requested_model,
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
            "requested_model": requested_model,
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
        # OS-44 (BUGFIX-I4-R1-REAL-PATH). Bind this Claude Code session to the Run it
        # just created, so a registered turn-end Stop hook knows which Run to gate this
        # session's turn ends on. Best-effort by design: outside Claude Code there is no
        # session to bind, and a run must never fail to start because a convenience
        # binding could not be written -- the cost of a missing one is never a hook that
        # guesses: a registered hook BLOCKS this session's turn ends, up to its cap,
        # while the project holds Run state or its Run state cannot be read.
        self._bind_turn_boundary_session()
        self.requested_phases = tuple(requested_phases)
        self._signals = []
        self._ledger = {}
        # OS-44. Run-scoped for the same reason self._ledger above is: a delivery id
        # belongs to the Run whose mailbox produced it, and carrying one Run's
        # acknowledged deliveries into the next would make a genuinely new delivery
        # read as a replay.
        self._deliveries = {}
        # OS-44 (BUGFIX-I1-G1-2). A Run this process just created cannot have a
        # predecessor process, so its recovery is complete by construction. Recorded
        # explicitly rather than left empty so _check()'s restore gate is satisfied by
        # a fact, not by a filesystem scan of an audit that cannot exist yet.
        self._deliveries_restored_for = self.run_id or ""
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
        # OS-49 BUGFIX (review B1). The model state is RUN-SCOPED and must be cleared HERE,
        # at the run boundary, beside every other per-run reset above -- not only in
        # `finish()`.
        #
        # `finish()` is the CLEAN exit and is not reached by a run that fails, is abandoned,
        # or raises on the way out; `start_run()` is the one point every run passes through
        # by construction. Clearing only on the way out therefore left a window: one
        # harness instance (which is the normal shape -- `run_runtime_scenarios()` drives
        # A..I on a single instance) whose run 1 died before `finish()` carried run 1's
        # model records into run 2, where leg (k) read a previous run's resolved model as
        # run 2's drift baseline and REFUSED a legitimate verification as
        # `model_selection_ambiguous`. The F-002 run filter in
        # `_counterpart_model_identity()` covered the counterpart read only;
        # `_model_session_identity` is keyed on the TERMINAL and `_model_pending_evidence`
        # carries no run scope at all, so neither was covered by it.
        #
        # Resetting rather than run-scoping each read is deliberate, and is the direction
        # the review asked for first: a filter makes a leaked record INVISIBLE at one site
        # and leaves it live for every site added later, while clearing the state makes the
        # leak unrepresentable. With these five statements the maps are run-scoped by
        # construction, which is also what lets the B2 history be append-only without
        # becoming permanent -- `for the life of the run` is enforced right here.
        self._model_identity = {}
        self._model_pending_evidence = {}
        self._model_session_identity = {}
        self._model_role_history = {}
        self._model_session_history = {}
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
        title: str | None = None,
        worktree: str = "current",
    ) -> str:
        """`title` and `worktree` are OS-31 seams, both defaulting to today's behaviour.

        The hard-coded `fake-{role}-{iteration}` title is NOT run-unique -- two runs, or
        two iterations of two runs, collide on it -- and the hard-coded `--worktree current`
        is an ALIAS that the *reading* process re-resolves, so persisting it persists no
        worktree at all. OS-31 passes a run-unique title and the stable
        `id:<repo-id>::<path>` selector it journalled before the Task was created, so a
        successor Coordinator can enumerate the terminal in the scope it was really made in.
        Every existing call site omits both and binds unchanged.
        """
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
        # OS-49. The MODEL this role is routed to, recorded as a SEPARATE ledger field.
        # The string passed to `terminal create --command` below is UNCHANGED: a model is
        # never concatenated into it. Doing so would make the model shell input AND would
        # silently change the reuse gate's condition-2 key, so that one command's two
        # models would look like two different executables to a gate that is supposed to
        # judge them on a second axis.
        requested_model = self.resolved_agent_model(role, phase)
        created = self.call(
            "terminal",
            "create",
            "--worktree",
            worktree,
            "--title",
            title if title is not None else f"fake-{role}-{iteration}",
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
            requested_model=requested_model,   # OS-49: condition 9's declared half
            model_state=(
                MODEL_EVIDENCE_REQUESTED if requested_model else MODEL_EVIDENCE_NONE
            ),
        )
        return handle

    @staticmethod
    def _barrier_phase(role: str, phase: str | None) -> str:
        """The phase name the pre-delivery barrier keys this attempt to.

        A Final Adversarial Review attempt carries no workflow phase, so it keys to the
        reserved final-review slot -- which is also the slot its routing entry lives in.
        Every other role keys to its own phase, and "" stays "" so the barrier's
        fail-closed argument check can see a genuinely missing value.
        """
        if role.startswith("final"):
            return phase or FINAL_REVIEW_PHASE
        return phase or ""

    @staticmethod
    def _routing_key(role: str, phase: str = "") -> tuple[str, str]:
        """The ONE role -> (phase, routing-role) mapping, factored out of one place.

        OS-49 made this a correctness property rather than tidiness: a command and a
        model read through two independent lookups could disagree, and the disagreement
        would be invisible in the ledger and in the durable provenance row. Both
        accessors below, and the barrier, go through this.

        OS-49 iteration 2 (review F-001). The PHASE is part of the mapping, not just the
        role spelling. A Final Adversarial Review is dispatched as role "reviewer" in
        phase `final_review` -- the only spelling any production initiator produces, and
        the one `_barrier_phase()` already maps onto the reserved slot -- so keying the
        routing role off the role string alone sent that attempt to a ("final_review",
        "reviewer") entry that does not exist. The lookup missed, the barrier returned at
        `if not requested`, and the declared Final Reviewer model was never requested,
        never verified and never recorded. `final_review` is a reviewer-only gate over a
        whole run (task_context.FINAL_REVIEW_PHASE), so it has exactly ONE routing slot
        and either spelling of the role resolves to it.

        OS-49 BUGFIX (review N2). What this CHANGES, stated rather than understated. The
        iteration-2 text claimed it "widens the slot's REACHABILITY only", and that was
        wrong by omission: every accessor built on this mapping -- `_routing_entry_for()`,
        and therefore `resolved_agent_command()` and `resolved_agent_model()` -- now
        RESOLVES DIFFERENTLY for a Final Adversarial Review attempt. Called with
        `phase="final_review"` and role `"reviewer"` (the spelling production initiators
        produce) it used to look up a ("final_review", "reviewer") entry that does not
        exist and return `""`; it now returns the Final Reviewer slot's command and model.
        A caller that relied on `""` to mean "no routing" for Final Review gets a real
        command instead. That is the intended fix -- the declared Final Reviewer model was
        otherwise never requested, never verified and never recorded -- but it is a
        behaviour change in the public accessors, not merely a wider lookup.
        What is genuinely unchanged: the PAIR-admission rule. The Final Reviewer stays
        outside it, because `final_reviewer` has no counterpart entry to look up -- which
        is a property of the role map in the barrier, not of this mapping.
        """
        if role.startswith("final") or phase == FINAL_REVIEW_PHASE:
            return FINAL_REVIEW_PHASE, "final_reviewer"
        return phase, ("reviewer" if role.endswith("reviewer") else "worker")

    @staticmethod
    def _routing_is_model_aware(routing: Any) -> bool:
        """Does `routing` declare a model anywhere -- answered FAIL-CLOSED (review N1).

        `getattr(routing, "is_model_aware", False)` was the wrong default for a predicate
        that gates a fail-closed barrier. On the real `RunRouting` it is a property and
        always answers (`agent_profile.RunRouting.is_model_aware`), so no legitimate
        routing's behaviour changes here. The exposure is the object that CANNOT answer: a
        duck-typed stand-in, a partially constructed routing, a shape some future caller
        passes in. `False` read every one of those as "this run declares no model", which
        skipped Gate B's argument check silently -- and a guard that disappears when it
        cannot evaluate itself is not a guard.

        So an object that cannot answer is treated as model-AWARE, which is the strict
        reading in both places this is used: in the barrier it keeps the (role, phase,
        attempt) identity REQUIRED, so a malformed routing yields a refused dispatch
        (observable) instead of an unverified delivery (not observable); in the provenance
        row it keeps the row being EMITTED, so the durable evidence errs towards recording
        more rather than less. A raising property is the same case as a missing one -- the
        question went unanswered -- and an undeclared fact is unknown, not false.

        `None` is the one shape that answers `False`, and it is not a malformed object: it
        is the explicit legacy sentinel for "no routing at all", checked by every caller
        before this point and byte-identical to pre-OS-49 behaviour.

        OS-49 BUGFIX (review 5970292670, N-2). The TRUTH-VALUE EVALUATION is inside the
        guard too, and that is the whole of this fix. The `except` previously covered only
        the attribute READ, so the three shapes the helper reasons about were not treated
        alike: a missing `is_model_aware` and a raising PROPERTY both reached the
        conservative answer, while an attribute that EXISTS and returns an object whose
        `__bool__` raises escaped from `return bool(aware)` as a raw exception -- out of a
        `@staticmethod` predicate, into a fail-closed barrier and into the settled-dispatch
        logging funnel. All three are the same fact: the question went unanswered. An
        unanswered question is answered model-AWARE here, never by an exception.

        The capture stays `Exception`, deliberately, and must not be widened. The width is
        the control-flow boundary review F-002 established across this module: a
        KeyboardInterrupt, SystemExit or GeneratorExit raised from inside a property or a
        `__bool__` is an operator or interpreter decision, not an unanswered question, and
        it leaves as itself. `None`, and a genuine `True`/`False`, are untouched.
        """
        if routing is None:
            return False
        try:
            aware = getattr(routing, "is_model_aware", _ABSENT)
            if aware is _ABSENT:
                return True                 # no such attribute: answered nothing
            return bool(aware)              # a raising `__bool__`: also answered nothing
        except Exception:                   # noqa: BLE001 - see the docstring's N-2 note
            return True

    def _routing_entry_for(self, role: str, phase: str = "") -> Any | None:
        if self.agent_routing is None:
            return None
        return self.agent_routing.for_role(*self._routing_key(role, phase))

    def resolved_agent_command(self, role: str, phase: str = "") -> str:
        """The resolved command for `role` in `phase`, or "" when there is no routing.

        Reads the run's already-materialized routing; resolves nothing. "" is the
        legacy answer, and every caller falls back to what it used before -- which is
        what keeps a scenario that selected no profile dispatching the same commands
        and recording the same ledger values as before this method existed.

        OS-49 BUGFIX (review N2). The old "Behaviour UNCHANGED by OS-49" line was true of
        iteration 1 and stopped being true when `_routing_key()` mapped
        `phase == final_review` onto the reserved Final Reviewer slot. ONE case moved, and
        only one: called for a Final Adversarial Review -- role `"reviewer"` with
        `phase="final_review"`, or any role spelled `final*` -- this used to miss the
        lookup and return `""`, and now returns the Final Reviewer slot's resolved command.
        Every other (role, phase) answers exactly what it answered before, and a run that
        selected no profile still gets `""` everywhere.
        """
        entry = self._routing_entry_for(role, phase)
        return entry.command if entry is not None else ""

    def resolved_agent_model(self, role: str, phase: str = "") -> str:
        """The resolved MODEL for `role` in `phase`, read from the SAME entry as the
        command, or "" when none is declared.

        "" is the legacy answer and the overwhelmingly common one: a v1 document, a
        model-less v2 document and a profile-omitted run all produce it, and every
        model-aware behaviour in this class is gated on a non-empty value.
        """
        entry = self._routing_entry_for(role, phase)
        return entry.model if entry is not None else ""

    def routing_binding(self) -> dict[str, str]:
        """The run's ROUTING IDENTITY as the launch record's own closed cells.

        ONE derivation, on the object that owns BOTH `agent_routing` and `model_driver`,
        read by the RECORDER (`launcher.build_orca_adapter`) and by the CHECKER
        (`OrcaAdapter._assert_preparation_binding`) -- so the two cannot drift, and
        neither holds a copy of the model-awareness predicate.

        Its key set is EXACTLY `pause_store.PAIR_LAUNCH_IDENTITY_KEYS`, which is derived
        from `PAIR_BINDING_KEYS` rather than re-spelled. That is what makes the checker's
        comparison total: it iterates the tuple, so every cell this method emits -- the
        DRIVER cell included -- is compared, and a cell added here in a later schema
        version cannot be silently left out of the check.

        Side-effect-free: it reads routing fields and derives the driver's class identity.
        """
        routing = self.agent_routing
        aware = routing is not None and self._routing_is_model_aware(routing)
        if routing is not None:
            cells = {
                "runtime": str(getattr(routing, "runtime", "") or ""),
                "profile_name": str(getattr(routing, "profile_name", "") or ""),
                "profile_source": str(getattr(routing, "profile_source", "") or ""),
                "routing_schema_version": str(
                    int(getattr(routing, "schema_version", 0) or 0)
                ),
            }
        else:
            cells = {
                "runtime": "",
                "profile_name": "",
                "profile_source": "",
                "routing_schema_version": "",
            }
        if not aware:
            # A POSITIVE legacy statement: no routing identity to bind, and the empty
            # cells SAY so rather than omitting a key from the closed set.
            return {
                **cells,
                "routing_digest": "",
                "model_aware": "false",
                "driver_type_id": "",
            }
        identity = {
            "runtime": cells["runtime"],
            "profile_name": cells["profile_name"],
            "profile_source": cells["profile_source"],
            "schema_version": cells["routing_schema_version"],
            "requested_phases": sorted(str(p) for p in routing.requested_phases),
            "entries": sorted(
                [
                    str(entry.phase),
                    str(entry.role),
                    str(entry.command),
                    str(entry.model),
                    bool(entry.required),
                    bool(entry.resolved),
                ]
                for entry in routing.entries
            ),
        }
        return {
            **cells,
            "model_aware": "true",
            "routing_digest": hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest(),
            # The ONE derivation, in `agent_profile`. `type(...).__name__` is NOT used
            # here or anywhere else: two different classes can share it.
            "driver_type_id": driver_type_id(self.model_driver),
        }

    def _same_command_pair(
        self, *, phase: str, command: str, counterpart_role: str
    ) -> bool:
        """The ONE same-command pair predicate. Read by THREE call sites, so no two
        copies can disagree about which pairs the rule covers -- the defect this module's
        own docstring names one layer up.

        Scoped exactly as Gate A's pair check is: to a counterpart that is REQUIRED and
        resolved. At LOW risk the Reviewer entry exists but is optional and no Reviewer is
        ever dispatched, so there is no pair to admit.
        """
        routing = self.agent_routing
        if routing is None:
            return False
        counterpart_entry = routing.for_role(phase, counterpart_role)
        return bool(
            counterpart_entry is not None
            and counterpart_entry.required
            and counterpart_entry.resolved
            and counterpart_entry.command == command
        )

    def pair_admission_required(self, role: str, phase: str = "") -> bool:
        """PUBLIC, side-effect-free: does a delivery to `(role, phase)` require a
        positively verified COUNTERPART? Raises nothing, mints nothing, observes nothing.

        `False` on a legacy run, on a role with no declared model, for the Final Reviewer
        (which is deliberately outside the pair rule) and for a distinct-command pair.
        `OrcaAdapter` reads THIS -- it keeps no copy.
        """
        routing = self.agent_routing
        if routing is None:
            return False                       # legacy: no pair rule at all
        barrier_phase = self._barrier_phase(role, phase)
        entry = self._routing_entry_for(role, barrier_phase)
        if entry is None or not entry.model:
            return False                       # evidence state `none`
        routing_role = self._routing_key(role, barrier_phase)[1]
        counterpart_role = {"worker": "reviewer", "reviewer": "worker"}.get(routing_role)
        if counterpart_role is None:
            return False                       # the Final Reviewer
        return self._same_command_pair(
            phase=barrier_phase,
            command=entry.command,
            counterpart_role=counterpart_role,
        )

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

    # ---- OS-49 GATE B: the pre-delivery model-identity barrier ------------------------

    def _mint_model_selection_ticket(
        self,
        *,
        task_id: str,
        terminal: str,
        role: str,
        phase: str,
        attempt: int,
        command: str,
        requested_model: str,
    ) -> tuple[ModelSelectionTicket, int]:
        """Mint ONE single-use ticket and return it with the ordinal it was issued at.

        The returned `t` is the counter's value AFTER minting, so a conforming driver's
        two `stamp()` calls must yield exactly `t + 1` then `t + 2`, and the counter must
        end at `t + 2`. That triple is the whole ordering proof.
        """
        self._model_selection_seq += 1
        issued_at = self._model_selection_seq
        token = ":".join(
            (
                self.run_id or "",
                task_id,
                terminal,
                role,
                phase,
                str(attempt),
                str(issued_at),
            )
        )
        self._model_selection_open_tokens.add(token)

        def stamp() -> int:
            if token not in self._model_selection_open_tokens:
                raise OrcaRuntimeError(
                    "model-selection ticket is revoked: a stamp may only be drawn "
                    "during the single select_and_verify() call the barrier minted it "
                    f"for (token={token!r})"
                )
            self._model_selection_seq += 1
            return self._model_selection_seq

        ticket = ModelSelectionTicket(
            run_id=self.run_id or "",
            task_id=task_id,
            terminal=terminal,
            role=role,
            phase=phase,
            attempt=attempt,
            command=command,
            requested_model=requested_model,
            token=token,
            stamp=stamp,
        )
        return ticket, issued_at

    def _revoke_model_selection_ticket(self, ticket: ModelSelectionTicket) -> None:
        """Close the stamp window. Called in a `finally:`, so a raising driver cannot
        leave a live stamp behind to spend inside the NEXT attempt's window."""
        self._model_selection_open_tokens.discard(ticket.token)

    def _model_refusal(
        self,
        reason: str,
        *,
        role: str,
        phase: str,
        attempt: int,
        command: str,
        requested_model: str,
        evidence: "ModelEvidence | None" = None,
        ticket: "ModelSelectionTicket | None" = None,
        expected_window: tuple[int, int] | None = None,
        drawn_to: int | None = None,
        detail: str = "",
    ) -> OrcaRuntimeError:
        """The barrier's refusal, carrying the WHOLE diagnosis.

        Shaped like the OS-41 acknowledgement gate's: a reader must be able to see which
        LEG of the lifecycle failed and why, not merely that a model was unverified.
        """
        parts = [
            f"{reason}: refusing to deliver a task before the requested model is "
            "positively verified",
            f"role={role!r}",
            f"phase={phase!r}",
            f"attempt={attempt}",
            f"command={command!r}",
            f"requested_model={requested_model!r}",
        ]
        if ticket is not None:
            parts.append(f"minted_token={ticket.token!r}")
        if expected_window is not None:
            parts.append(
                f"expected_stamps={expected_window[0]}->{expected_window[1]}"
            )
        if drawn_to is not None:
            parts.append(f"counter_after_driver={drawn_to}")
        if evidence is not None:
            # OS-49 BUGFIX (review 5970292670, N-1, fix point 3). EVERY cell below renders
            # a field of the DRIVER'S `ModelEvidence`, and `ModelEvidence` is a plain
            # frozen dataclass with no field validation, so each one is an arbitrary
            # object whose `__repr__`, `__str__` or `__format__` may raise. Eager `!r` and
            # `{}` therefore let the DIAGNOSTIC replace the refusal it exists to explain:
            # the specific reason -- `model_selection_unsupported` for an observation
            # method outside the closed set, say -- was lost and the caller received the
            # generic malformed-evidence normalization from the B3 boundary instead. The
            # review states the rule directly: keep diagnostics subordinate to the failure
            # being reported.
            #
            # `safe_repr` / `safe_text` are byte-identical to `!r` / `str()` for every
            # value that renders at all, so no existing refusal message moves. The
            # `observed_at` join is included: `":".join()` over a non-string raises
            # `TypeError`, which is the same escape with a different spelling.
            parts.extend(
                (
                    f"evidence_state={safe_repr(evidence.state)}",
                    f"resolved_model={safe_repr(evidence.resolved_model)}",
                    f"request_method={safe_repr(evidence.request_method)}",
                    f"selection_token={safe_repr(evidence.selection_token)}",
                    f"request_stamp={safe_text(evidence.request_stamp)}",
                    f"observe_stamp={safe_text(evidence.observe_stamp)}",
                    f"observation_method={safe_repr(evidence.observation_method)}",
                    f"capability={safe_repr(evidence.capability)}",
                    "observed_at="
                    + ":".join(
                        (
                            safe_text(evidence.observed_at_run),
                            safe_text(evidence.observed_at_task),
                            safe_text(evidence.observed_at_terminal),
                            safe_text(evidence.observed_at_role),
                            safe_text(evidence.observed_at_phase),
                            safe_text(evidence.observed_at_attempt),
                        )
                    ),
                )
            )
        else:
            parts.append("evidence=none")
        if detail:
            parts.append(detail)
        return OrcaRuntimeError("; ".join(parts))

    #: The terminal-row cells the barrier (and `_rebind_model_evidence()`) write, so a
    #: rollback restores a row to exactly the shape it had before this attempt touched it.
    _MODEL_ROW_CELLS = (
        "resolved_model",
        "model_state",
        "model_request_method",
        "model_request_evidence",
        "model_observed_at_dispatch",
    )

    #: The value each of those cells carries on a row that has NO model evidence -- the
    #: exact shape `register_terminal()` creates. Not a `pop`, deliberately: reuse
    #: condition 9 and the durable provenance row read these keys by name, so removing
    #: them would turn "this session has no verified model" into a KeyError. Written by
    #: the two paths that have to un-advertise evidence: a rollback whose snapshot
    #: predates the row's existence (review N1) and `_stale_model_evidence()` (review B1).
    _MODEL_ROW_CLEARED = {
        "resolved_model": "",
        "model_state": MODEL_EVIDENCE_NONE,
        "model_request_method": "",
        "model_request_evidence": "",
        "model_observed_at_dispatch": "",
    }

    def _model_evidence_snapshot(
        self, *, terminal: str, role: str, phase: str
    ) -> dict[str, Any]:
        """Everything an accepted barrier would OVERWRITE for this attempt, as it is now.

        OS-49 BUGFIX (review N1). Model verification is recorded at the barrier, which is
        `start_worker()`'s FIRST statement, and the rest of that method can still refuse
        or fail -- the own-handle refusal, the TUI-idle wait, a `worker-start` that never
        reaches a ready worker, the OS-41 acknowledgement gate, a failing `dispatch`. The
        accepted record outlived all of those, and because it is exactly what grants pair
        admission and what reuse condition 9 reads, a delivery that never happened could
        authorize a later one as if it had.

        A snapshot/restore rather than a delete, deliberately: a rollback must leave the
        state EXACTLY as it was immediately before this attempt's barrier ran. Deleting
        would also discard a record an earlier, separate and successful
        `verify_model_identity()` pre-pass legitimately earned, which would refuse a
        retry that should be allowed. Restoring makes the lifetime of this attempt's
        evidence the lifetime of this attempt's delivery, and nothing wider.
        """
        identity_key = (phase, self._routing_key(role, phase)[1])
        row = self._terminals.get(terminal)
        return {
            "identity_key": identity_key,
            "identity": self._model_identity.get(identity_key, _ABSENT),
            "pending": self._model_pending_evidence.get(terminal, _ABSENT),
            "session": self._model_session_identity.get(terminal, _ABSENT),
            "terminal": terminal,
            "row_cells": (
                None if row is None
                else {cell: row.get(cell, _ABSENT) for cell in self._MODEL_ROW_CELLS}
            ),
        }

    def _restore_model_evidence(self, snapshot: dict[str, Any] | None) -> None:
        """Undo every write an accepted barrier made for one attempt. Total, and silent.

        Called from the failure path of `start_worker()` only, where an exception is
        already on its way out: this must never replace that exception with one of its
        own, so every lookup is absence-tolerant.
        """
        if not snapshot:
            return
        for mapping, key, value in (
            (self._model_identity, snapshot["identity_key"], snapshot["identity"]),
            (self._model_pending_evidence, snapshot["terminal"], snapshot["pending"]),
            (self._model_session_identity, snapshot["terminal"], snapshot["session"]),
        ):
            if value is _ABSENT:
                mapping.pop(key, None)
            else:
                mapping[key] = value
        cells = snapshot["row_cells"]
        row = self._terminals.get(snapshot["terminal"])
        if row is None:
            return
        if cells is None:
            # OS-49 BUGFIX (review N1, second round). `row_cells is None` does not mean
            # "nothing to undo", it means the row DID NOT EXIST when the snapshot was
            # taken -- and `start_worker()`'s `_attach_terminal()` creates one for an
            # unseen handle, then `_rebind_model_evidence()` writes this attempt's
            # accepted evidence onto it, all of it AFTER the barrier and all of it inside
            # the delivery this rollback is undoing. Returning early left that brand-new
            # row advertising resolved_model/model_state/model_observed_at_dispatch for a
            # delivery that never completed, which is the contradictory state reuse
            # condition 9 and the provenance row would read as earned.
            #
            # The cells are CLEARED to their creation-time shape rather than the row
            # being deleted: the rollback did not create the row (`_attach_terminal()`
            # did, as an adoption, and the adoption itself is a real observation of a
            # handle this Coordinator now owns), so removing it would discard axis (c2)
            # role/origin evidence that exists nowhere else. Only the MODEL cells belong
            # to this attempt, so only they are undone.
            row.update(self._MODEL_ROW_CLEARED)
            return
        for cell, value in cells.items():
            if value is _ABSENT:
                row.pop(cell, None)
            else:
                row[cell] = value

    def _counterpart_model_identity(
        self, *, phase: str, routing_role: str
    ) -> tuple[str | None, Any]:
        """The counterpart role of `(phase, routing_role)` and its RUN-SCOPED record.

        One derivation, read by both halves of pair admission -- the PRE-selection
        session check and the POST-selection effective-identity comparison (review B1).
        They used to be one block, and splitting them without extracting this would have
        left two copies of the run-scope filter free to disagree about which records
        count.

        The run scope is review F-002's and is unchanged: model evidence is RUN-scoped,
        and a record observed in another run says nothing about whether THIS run has a
        counterpart session at all. Returning `None` for it is the fail-closed reading --
        a same-command delivery is then refused as `MODEL_SELECTION_PAIR_UNADMITTED`.

        `(None, None)` means this role has no counterpart in the rule at all (the Final
        Reviewer), which is deliberately outside it.
        """
        counterpart_role = {"worker": "reviewer", "reviewer": "worker"}.get(routing_role)
        if counterpart_role is None:
            return None, None
        counterpart = self._model_identity.get((phase, counterpart_role))
        if counterpart is not None and counterpart.observed_at_run != (self.run_id or ""):
            counterpart = None
        return counterpart_role, counterpart

    def _stale_model_evidence(self, terminal: str) -> None:
        """Invalidate every model record that CLAIMS TO DESCRIBE this physical session.

        OS-49 BUGFIX (review B1). The one thing a refusal after `select_and_verify()` has
        run cannot do is pretend the session is untouched. Selection is the act that
        switches the session, so by the time any post-selection leg refuses, the physical
        model of `terminal` is whatever the driver left it on -- which is, at best,
        unknown to this harness and, at worst, exactly the model the refusal was about.
        Every record that was earned against the model this session USED to be on has
        therefore stopped describing it.

        Restoring such a record would be the defect: B1's reproduction is precisely a
        Reviewer record for `terminal` surviving a refused Worker attempt on `terminal`
        and then granting pair admission to a LATER delivery on a different session,
        while both physical sessions sat on one model. So this stales rather than
        restores, and it is keyed on the TERMINAL rather than on `(phase, role)`: a
        record for ANY phase or role that names this session as the place it was observed
        is equally no longer a description of it.

        What it does NOT touch, and must not: records observed at OTHER terminals. Those
        sessions were not asked to select anything by this attempt, so their evidence
        still describes them. Nor `requested_model` on the row -- that is the routing's
        DECLARATION, not evidence, and it did not become false.

        Recovery is a positive re-verification, not a restore: the caller runs
        `verify_model_identity()` against the session again, the driver selects and
        observes again, and the record that results describes the session as it ACTUALLY
        is. Until then a same-command counterpart delivery is refused as
        `MODEL_SELECTION_PAIR_UNADMITTED`, which is the existing vocabulary member for
        "the counterpart has no positively verified model evidence" -- which, after
        staling, is the literal truth. No new refusal reason is needed or added.

        OS-49 BUGFIX (review B2). What it also does NOT touch, and must not:
        `_model_role_history` / `_model_session_history`. Revoking AUTHORITY is this
        method's whole job; erasing HISTORY was its defect. The three maps below answer
        "may this evidence admit a pair or authorize a reuse?", and after a selection that
        failed validation the honest answer is no. The two history maps answer "what did
        this slot, and this physical session, ever positively resolve to in this run?", and
        a failed validation does not make an earlier positive observation un-happen. Deleting
        them turned the FIRST drift refusal into a laundering step for the second attempt:
        accept model-A, refuse model-B, retry model-B and be accepted because the only
        record that knew about model-A had just been deleted by the refusal. Drift legs (i)
        and (k) therefore read history, which this method leaves alone, and a drifting
        session's remedy is a new session rather than a retry on a cleared slate.

        OS-49 BUGFIX (review 5970292670, N-3). What "a new session" does and does not buy,
        qualified -- the superseded sentence stopped at "a new session" and read as though
        any fresh session recovers a drifted role. It does not. A fresh session clears leg
        (k) only, which is keyed on the physical TERMINAL and which no history names for a
        terminal that has never been verified. Leg (i) is keyed on the (phase, ROUTING
        ROLE) and reads `_model_role_history`, which a new session does not touch, so
        WITHIN THIS RUN the fresh session must still resolve to that ROLE'S ESTABLISHED
        BASELINE. Resolving to a different model for the same role is refused on the new
        session exactly as it was on the old one. A role resolving to a different model is
        a NEW RUN's business, which is precisely why both history maps are run-scoped.
        This is a statement of what the two legs already do; neither check is relaxed.
        """
        self._model_session_identity.pop(terminal, None)
        self._model_pending_evidence.pop(terminal, None)
        for key in [
            key
            for key, evidence in self._model_identity.items()
            if evidence.observed_at_terminal == terminal
        ]:
            del self._model_identity[key]
        row = self._terminals.get(terminal)
        if row is not None:
            row.update(self._MODEL_ROW_CLEARED)

    def _gate_b_model_identity(
        self, *, task_id: str, terminal: str, role: str, phase: str, attempt: int
    ) -> dict[str, Any]:
        """GATE B: the ONE mandatory fail-closed barrier before BOTH delivery acts.

        A one-line reading of the shared core with `require_pair_admission=True`, which
        is the single difference between a DELIVERY and the public pre-pass: a delivery
        additionally requires that a same-command counterpart's model is already
        positively verified, so independence is never assumed on the strength of two
        declared tokens.

        Returns the pre-barrier snapshot `start_worker()` restores if the delivery it was
        taken for never completes (review N1). The barrier itself is unchanged: a REFUSAL
        still records nothing, so the snapshot matters only for the case the refusal
        vocabulary cannot cover -- an ACCEPTED model on a delivery that then failed.
        """
        snapshot = self._model_evidence_snapshot(
            terminal=terminal, role=role, phase=phase
        )
        self._verify_model_identity(
            task_id=task_id,
            terminal=terminal,
            role=role,
            phase=phase,
            attempt=attempt,
            require_pair_admission=True,
        )
        return snapshot

    def verify_model_identity(
        self,
        task_id: str,
        terminal: str,
        *,
        role: str,
        phase: str,
        attempt: int,
    ) -> None:
        """The model-selection PRE-PASS: request, positively verify, record, deliver
        NOTHING.

        Exists because of review F-001. A same-command Worker/Reviewer pair cannot be
        admitted by the Worker's own barrier -- at that moment the Reviewer session does
        not exist, nothing has been observed for it, and two distinct declared tokens are
        not evidence of two agents. So the admission has to happen BEFORE the first
        delivery of either role, and that requires a step that can verify a role's model
        without dispatching to it. This is that step.

        The lifecycle for a same-command model-aware pair is therefore:

            create/attach BOTH sessions
              -> verify_model_identity(worker)     request -> positively verify -> record
              -> verify_model_identity(reviewer)   request -> positively verify -> record
                                                   (and compare, on RESOLVED values)
              -> start_worker(...)                 Gate B re-verifies and delivers

        Runs exactly the same legs (a)-(i) as Gate B, through the same ticket, the same
        ordinal window and the same closed vocabularies, and records the identity only on
        acceptance -- so a refused pre-pass leaves no trace a later round could read as
        earned. The ONLY relaxation is `require_pair_admission=False`: the first role
        verified necessarily has no counterpart record yet, and requiring one here would
        make the pre-pass unsatisfiable. It cannot be used to skip the delivery-time
        requirement, because `start_worker()` applies that requirement itself, on every
        role and every round, whatever the pre-pass did.

        Idempotent in the sense that matters: a second pre-pass or the later Gate B
        verification for the same (phase, role) must resolve to the same model or leg (i)
        refuses with `model_selection_ambiguous`.

        A no-op on a legacy run and on a role with no declared model, exactly as Gate B
        is -- so a caller may call it unconditionally.

        OS-14: its PRODUCTION caller is `OrcaAdapter._prepare_pair`, which calls it for
        BOTH roles of a gate round, on every pass, before the first delivery of either --
        so the next reader does not have to rediscover that this pre-pass now has one.
        """
        self._verify_model_identity(
            task_id=task_id,
            terminal=terminal,
            role=role,
            phase=phase,
            attempt=attempt,
            require_pair_admission=False,
        )

    def _verify_model_identity(
        self,
        *,
        task_id: str,
        terminal: str,
        role: str,
        phase: str,
        attempt: int,
        require_pair_admission: bool,
    ) -> None:
        """The shared model-selection verification core.

        Gate B reads it at `start_worker()`'s entry, which is the single point strictly
        before
        the rung-3 `worker-start --terminal` delivery AND the rung-4
        `dispatch` + `terminal send` delivery, and which both centralized dispatch
        initiators (`run_existing_task`, `observe_unexpected_exit`) and all three roles
        and every round kind necessarily pass. A caller-supplied hook would not be:
        `terminal_observer` is optional and absent from `observe_unexpected_exit`, which
        is the exact bypass class that once let that initiator reach `start_worker`
        without the OS-29 B1 guard.

        Nothing is delivered when this raises: no `worker-start`, no `dispatch` and no
        `terminal send` has run at the point it does.

        What the harness OWNS here, all of it integer arithmetic and string equality over
        values this harness itself issued or defined: (a) a request leg exists at all;
        (b) its `request_method` and `capability` are members of closed sets defined
        here; (c) its `selection_token` is the token THIS barrier minted; (d) its two
        ordinals are the two this barrier's counter actually issued, in order, exactly
        twice; (e) the state is exactly `verified`; (f) `resolved_model` is non-empty and
        well-formed; (g) `requested_model` echoes back unchanged; (h) freshness against
        the six-part attempt key; (i) consistency with any earlier record for this
        `(phase, role)`; (j) the effective-identity rule, re-applied to RESOLVED values,
        which on a DELIVERY additionally REQUIRES a same-command counterpart to be
        positively verified already (`require_pair_admission`).

        What it does NOT own, deliberately: whether a resolved value SATISFIES the
        request. Only something that can observe a provider's resolution can know that
        (`--model opus` has been measured resolving to `claude-opus-5`), so the DRIVER
        owns satisfaction and must report `mismatch` rather than silently accepting. The
        harness implements no alias table and needs no provider knowledge.

        What no seam can prove, stated rather than hidden: a driver that deliberately
        LIES -- draws both ordinals in order and reports a request it never issued -- is
        undetectable from inside the harness. What is guaranteed is that the request is a
        required, separately attested leg, so an implementation cannot satisfy the
        contract by observation alone; that the attestation is bound to an attempt key
        and an ordinal window the driver cannot manufacture, so evidence cannot be
        carried over from a previous attempt, another session or an unrequested default
        state; and that the only drivers permitted to attest in this release are the
        deterministic/fake seam, i.e. reviewable test code inside this repository.

        The pre-pass inherits exactly that limit and adds no new one: a caller can run
        `verify_model_identity()` against a counterpart session it then never uses, and
        the Worker would deliver. That is the same trust placed in the driver, not a new
        hole -- the counterpart's own later delivery still verifies, and leg (i) refuses
        any model that drifted. What the pre-pass removes is the ability to deliver a
        same-command Worker with NO counterpart evidence at all, which required no
        misbehaviour from anyone and was the iteration-1 defect.
        """
        routing = self.agent_routing
        if routing is None:
            return                              # legacy path: byte-identical to today
        # The fail-closed ARGUMENT check comes FIRST, and is keyed on the RUN being
        # model-aware rather than on this role's entry. It has to: the entry lookup needs
        # the phase, so checking the arguments afterwards would let an omitted `phase`
        # make the lookup MISS and the barrier skip itself -- silently, on a run that
        # declares models. A future caller that forgets to thread the identity therefore
        # gets a refused dispatch, which is observable, rather than an unverified
        # delivery, which is not.
        if self._routing_is_model_aware(routing) and not (role and phase and attempt):
            raise self._model_refusal(
                MODEL_SELECTION_UNVERIFIED,
                role=role,
                phase=phase,
                attempt=attempt,
                command="",
                requested_model="",
                detail="this run's routing declares a model, and the barrier was not "
                "given the (role, phase, attempt) identity this attempt's model evidence "
                "must be keyed to",
            )
        entry = self._routing_entry_for(role, phase)
        command = entry.command if entry is not None else ""
        requested = entry.model if entry is not None else ""
        if not requested:
            return                              # state `none`: no model for this role
        # ---- from here a model IS declared, so nothing below may be skipped ----------
        # OS-49 BUGFIX (review M3). The SHAPE check, through the one capability
        # derivation Gate A also reads (`agent_profile.model_selection_capabilities`), so
        # the declaration gate and this barrier cannot disagree about what counts as a
        # driver. It used to ask `self.model_driver is None`, which three different broken
        # drivers all passed: one with no `select_and_verify` at all, one whose
        # `select_and_verify` is a non-callable attribute, and -- because the call below
        # had no handler -- one that raises. Each escaped as a raw AttributeError,
        # TypeError or arbitrary driver exception, i.e. OUTSIDE the closed OS-49 failure
        # vocabulary, which is precisely what a caller cannot branch on.
        #
        # Not merely "cannot observe": with no CALLABLE driver a selection cannot even be
        # REQUESTED, which is a stronger and differently-named failure.
        if MODEL_SELECTION_VERIFIED_CAPABILITY not in model_selection_capabilities(
            self.model_driver
        ):
            raise self._model_refusal(
                MODEL_SELECTION_UNSUPPORTED,
                role=role,
                phase=phase,
                attempt=attempt,
                command=command,
                requested_model=requested,
                detail="no model-selection driver with a callable select_and_verify() is "
                "wired in, so a selection cannot be requested on this placement "
                f"(driver={type(self.model_driver).__name__})",
            )
        # ---- PRE-SELECTION: the conflicts decidable WITHOUT the driver (review B1) ---
        # Everything from `_mint_model_selection_ticket()` down is AFTER the physical
        # session may have been switched, because `select_and_verify()` is the act that
        # switches it. A check that needs nothing the driver produces therefore has no
        # business running there: refusing afterwards means the session was mutated for
        # an attempt that was always going to be rejected, and B1 is exactly what that
        # window cost -- a Reviewer record for this terminal outliving a Worker attempt
        # that had already moved the terminal off the model the record names.
        #
        # BOTH halves of pair admission are that kind of check, and both are decided
        # here. (Iteration 1 hoisted only the first and its comment claimed that was "the
        # only one in this method"; final review R2 showed the claim was false and the
        # second one is corrected along with it.)
        #
        #   1. the SESSION conflict -- `counterpart.observed_at_terminal` vs `terminal`,
        #      two harness-held strings compared for equality.
        #   2. the MISSING same-command counterpart -- `counterpart is None`,
        #      `require_pair_admission`, and the counterpart entry's `required`,
        #      `resolved` and `command` against this `command`. Every one of them is
        #      harness state or a parameter of this call; not one is produced by the
        #      driver.
        #
        # The two differ in ONE respect, and deliberately. Check 1 is UNGATED by
        # `require_pair_admission` (review M5, unchanged): the defect it closes is
        # reachable through the PRE-PASS alone -- verify reviewer on term_1/model-B, then
        # verify worker on term_1/model-A -- after which the pair looked admitted to every
        # later delivery. Ungated by command, too: `Worker session != Reviewer session` is
        # a categorical Skill invariant, not a same-command special case, and a legitimate
        # pair always holds two distinct handles, so it can refuse nothing a correct
        # caller does.
        #
        # Check 2 IS gated by `require_pair_admission`, and that gate is load-bearing
        # rather than incidental. The pre-pass is HOW a caller bootstraps a same-command
        # pair: the first role it verifies necessarily has no counterpart evidence yet, so
        # a pre-pass that refused on counterpart absence would make same-command pairs
        # unroutable -- the condition would be unsatisfiable by construction. It is only a
        # DELIVERY that may not proceed on an unadmitted pair, which is exactly what
        # `require_pair_admission` names.
        #
        # For both checks the reason, the message and the gating are byte-for-byte what
        # the post-selection copies raised, so no caller's or test's expectation moves.
        # What moves is WHEN: no ticket has been minted, no ordinal drawn and no driver
        # called when either fires, which is the positive, assertable proof that the
        # mutation window is gone rather than merely narrowed. Both post-selection copies
        # REMAIN as re-entrancy backstops -- hoisting is an addition, not a move.
        routing_role = self._routing_key(role, phase)[1]
        counterpart_role, counterpart = self._counterpart_model_identity(
            phase=phase, routing_role=routing_role
        )
        if counterpart is not None and counterpart.observed_at_terminal == terminal:
            raise self._model_refusal(
                REASON_WORKER_REVIEWER_MUST_DIFFER,
                role=role,
                phase=phase,
                attempt=attempt,
                command=command,
                requested_model=requested,
                detail=f"the {counterpart_role} of phase {phase!r} was positively "
                f"verified on this very session {terminal!r} (resolved "
                f"{counterpart.resolved_model!r}); one physical session cannot be both "
                "sides of a pair, so its independence is not established no matter "
                "which models the two verifications resolved to",
            )
        if (
            counterpart is None
            and counterpart_role is not None
            and require_pair_admission
        ):
            # Check 2 (final review R2). The same-command scoping is read off the SAME
            # routing entry the post-selection copy reads, and with the same
            # required/resolved predicate, so the hoist and the backstop cannot disagree
            # about which pairs the rule covers.
            if self._same_command_pair(
                phase=phase, command=command, counterpart_role=counterpart_role
            ):
                raise self._model_refusal(
                    MODEL_SELECTION_PAIR_UNADMITTED,
                    role=role,
                    phase=phase,
                    attempt=attempt,
                    command=command,
                    requested_model=requested,
                    detail=f"the {counterpart_role} of phase {phase!r} shares this "
                    f"command {command!r} and has no positively verified model "
                    "evidence yet, so Worker/Reviewer independence rests on nothing "
                    "but two declared tokens -- which can alias onto one model. "
                    "Verify both effective identities with verify_model_identity() "
                    "before delivering either",
                )
        ticket, issued_at = self._mint_model_selection_ticket(
            task_id=task_id,
            terminal=terminal,
            role=role,
            phase=phase,
            attempt=attempt,
            command=command,
            requested_model=requested,
        )
        expected = (issued_at + 1, issued_at + 2)
        # OS-49 BUGFIX (review M3). A driver that RAISES is a failed selection, not a
        # harness defect, so it is normalized into the closed vocabulary here instead of
        # propagating whatever the driver chose to throw. `model_selection_unverified` is
        # the right member by the vocabulary's own lifecycle order: a selection was
        # requested and no resolved model was ever positively observed. The `finally:`
        # still revokes the ticket first, so a raising driver cannot leave a live stamp
        # behind for the NEXT attempt's window, and `Exception` deliberately does not
        # swallow KeyboardInterrupt or SystemExit.
        #
        # OS-49 iteration 2 (final review R1). Normalization and EVIDENCE INVALIDATION
        # are two different concerns, and the defect was that they shared one branch.
        # `except Exception` is the right NORMALIZATION boundary -- an interrupt or an
        # interpreter exit is not a failed model selection and must keep propagating as
        # itself -- but it is the wrong INVALIDATION boundary: a driver that calls
        # `ticket.stamp()` and then raises `KeyboardInterrupt` has begun selecting, so
        # the session may already be switched, yet the staling below used to be skipped
        # along with the normalization and every record naming the session stayed
        # authoritative. The invariant is about the SIDE EFFECT, not about the exception's
        # type: once selection may have occurred, no unsuccessful exit may leave the
        # pre-existing evidence authoritative.
        #
        # So the handler is `BaseException`, it stales FIRST, and only then decides
        # whether this exception is one the closed vocabulary speaks for. Non-`Exception`
        # control flow is re-raised untouched -- same type, same traceback, not wrapped in
        # `OrcaRuntimeError` -- so the M3-era interrupt/finality semantics are preserved
        # exactly. The nested `finally:` keeps the revoke-then-stale ORDER the ordinary
        # failure path already had, so no existing behaviour moves.
        driver_failure: Exception | None = None
        evidence: Any = None
        try:
            try:
                evidence = self.model_driver.select_and_verify(ticket)
            finally:
                self._revoke_model_selection_ticket(ticket)
        except BaseException as exc:                   # noqa: BLE001 - re-raised below
            # OS-49 BUGFIX (review B1), widened by R1. `select_and_verify()` RAN. A
            # driver that raised part-way through is the strongest case for staling, not
            # the weakest: it is the one path on which nobody -- not the driver, not this
            # harness -- can say whether the session was switched before the exception.
            # Unknown is not "unchanged", so every record describing this session is
            # invalidated here too, and recovery is a positive re-verification.
            self._stale_model_evidence(terminal)
            if not isinstance(exc, Exception):
                raise                      # KeyboardInterrupt / SystemExit, as themselves
            driver_failure = exc
        drawn_to = self._model_selection_seq
        if driver_failure is not None:
            raise self._model_refusal(
                MODEL_SELECTION_UNVERIFIED,
                role=role,
                phase=phase,
                attempt=attempt,
                command=command,
                requested_model=requested,
                ticket=ticket,
                expected_window=expected,
                drawn_to=drawn_to,
                detail="the model-selection driver raised "
                f"{safe_exception_text(driver_failure)}",
            ) from driver_failure

        # ---- POST-SELECTION VALIDATION BOUNDARY (OS-49 BUGFIX, review B3) ------------
        # ONE try/except around EVERY leg that runs after `select_and_verify()` has
        # returned, and the invariant it enforces is about the SIDE EFFECT rather than
        # about any particular leg: selection is the act that switches the physical
        # session, so once it has executed, NO unsuccessful exit from this method may
        # leave the current session evidence authoritative. Not "no refusal" -- no EXIT.
        #
        # What the previous round got wrong, and why the one-site fix was not enough.
        # `refuse()` stales, so every leg that goes THROUGH `refuse()` was covered; what
        # was not covered was a leg that never gets there because reading the evidence
        # itself raises. `MODEL_TOKEN_PATTERN.fullmatch(evidence.resolved_model)` with a
        # non-string `resolved_model` raises `TypeError` straight out of this method, past
        # every `refuse()` call site, and the session kept its authoritative record even
        # though the driver had already switched it. `ModelEvidence` is a plain frozen
        # dataclass with no field validation, so a driver may return ANY type in ANY field,
        # and a `TypeError` is reachable from any leg that does more than compare for
        # equality.
        #
        # So this is deliberately NOT a patch for `resolved_model=123`. Fixing that one
        # site would leave the next leg, and every leg added below in future, free to raise
        # its way past invalidation again. The boundary is placed where the SIDE EFFECT is
        # -- immediately after the driver call -- so a leg cannot be added inside it without
        # inheriting the invalidation, which is the same structural argument `refuse()`
        # itself was built on, applied to exceptions instead of refusals.
        #
        # `BaseException`, for the reason R1 established for the driver call: a
        # `KeyboardInterrupt` arriving mid-validation leaves the session just as switched
        # as a `TypeError` does, and the invariant is about the switch. Control flow that
        # the closed vocabulary does not speak for is re-raised as ITSELF after staling --
        # same type, same traceback -- so interrupt and interpreter-exit semantics do not
        # move.
        #
        # The accepted record writes at the very bottom are INSIDE the boundary too. They
        # are the last thing this method does, so nothing can fail after them today; having
        # them inside means that if anything ever does, the half-written authority is
        # invalidated rather than left standing.
        try:
            def refuse(reason: str, detail: str = "") -> OrcaRuntimeError:
                """Build a POST-SELECTION refusal -- and stale this session on the way out.

                OS-49 BUGFIX (review B1). Every `raise refuse(...)` below sits after
                `select_and_verify()` has returned, so by construction reaching any of them
                means the physical session may already carry a different model than the
                records naming it claim. `_stale_model_evidence()` is therefore part of what
                a post-selection refusal IS, not a thing each site must remember to do: a
                future leg added below inherits it, and a leg hoisted above `refuse`'s
                definition -- i.e. above the driver call -- correctly does not get it.

                Pre-selection refusals deliberately do NOT come through here. They use
                `self._model_refusal` directly, because nothing was requested of the session
                and its evidence still describes it; staling there would discard a record an
                earlier successful verification legitimately earned.
                """
                self._stale_model_evidence(terminal)
                return self._model_refusal(
                    reason,
                    role=role,
                    phase=phase,
                    attempt=attempt,
                    command=command,
                    requested_model=requested,
                    evidence=evidence if isinstance(evidence, ModelEvidence) else None,
                    ticket=ticket,
                    expected_window=expected,
                    drawn_to=drawn_to,
                    detail=detail,
                )

            # A wrong TYPE must be REFUSED, never raise a TypeError -- the same rule
            # reuse_eligible() already applies to a mis-typed ReuseObservation.
            if not isinstance(evidence, ModelEvidence):
                raise refuse(
                    MODEL_SELECTION_UNVERIFIED,
                    f"the driver returned {type(evidence).__name__}, not ModelEvidence",
                )
            # ---- the ATTESTED SNAPSHOT: each field read off the driver EXACTLY ONCE ----
            # OS-49 BUGFIX (final review F-012). `ModelEvidence` is `frozen`, but `frozen`
            # only blocks `__setattr__`; it does nothing about a SUBCLASS that overrides
            # `__getattribute__` or redeclares a field as a property. Admission above is
            # `isinstance`, so a subclass IS accepted -- and this barrier used to read each
            # attested field again at every leg that needed it, then store the DRIVER'S OWN
            # OBJECT into all three authority maps. So a subclass could return the
            # supported exact `str` for the two reads leg 2's vocabulary check makes, be
            # accepted, and return an arbitrary object on the next read: the value that was
            # VALIDATED was not the value that was STORED. That is N-1a again in
            # time-of-check/time-of-use form -- an arbitrary value reaching logging, reuse
            # and provenance code as authority, after the dispatch it authorized settled.
            #
            # The fix is CANONICALIZATION, not exact-class rejection. Admission stays
            # `isinstance` deliberately: production constructs the exact class only, and
            # every `ModelEvidence` subclass in this repository is an adversarial fixture
            # whose purpose is to reach LATER legs, so rejecting subclasses at the door
            # would change which leg they exercise rather than fix anything. Instead every
            # field is read HERE, once, into a local; every leg below validates the LOCAL;
            # and the accept block at the bottom stores a freshly constructed exact-class
            # `ModelEvidence` built only from these locals. The driver's OBJECT is never
            # authority, which closes the mutate-after-acceptance route in the same move as
            # the subclass route.
            #
            # OS-49 BUGFIX (final review F-013). That is the CONTAINER half, and on its own
            # it is not enough: the superseded wording here claimed no later read could
            # re-enter driver code, which was false, because the locals this block captures
            # are still whatever OBJECTS the driver put in the fields. The type gate
            # immediately below is the VALUE half, and the claim holds only with both: the
            # fifteen locals are proven exact `str` / `int` before any other leg touches
            # them, so the canonical record built from them carries no driver code at all.
            #
            # These reads are INSIDE the post-selection boundary, so a field whose read
            # raises an ordinary `Exception` still stales and still normalizes to
            # `model_selection_unverified`, and `KeyboardInterrupt`, `SystemExit` and
            # `GeneratorExit` still propagate as themselves. Hoisting moves WHEN such a
            # read raises, not WHAT the barrier does about it.
            #
            # `refuse()` above deliberately keeps rendering the DRIVER'S object: a refusal
            # diagnostic describes the value being REJECTED, which is by definition the
            # driver's and not any canonical form of it, nothing it renders becomes
            # authority, and a hostile renderer there is normalized by this very boundary
            # (F-001). The read-once obligation is about what becomes AUTHORITY.
            attested_state = evidence.state
            attested_requested_model = evidence.requested_model
            attested_resolved_model = evidence.resolved_model
            attested_selection_token = evidence.selection_token
            attested_request_method = evidence.request_method
            attested_request_stamp = evidence.request_stamp
            attested_observation_method = evidence.observation_method
            attested_observe_stamp = evidence.observe_stamp
            attested_capability = evidence.capability
            attested_observed_at_run = evidence.observed_at_run
            attested_observed_at_task = evidence.observed_at_task
            attested_observed_at_terminal = evidence.observed_at_terminal
            attested_observed_at_role = evidence.observed_at_role
            attested_observed_at_phase = evidence.observed_at_phase
            attested_observed_at_attempt = evidence.observed_at_attempt
            # ---- the TYPE GATE: no leg touches a value whose TYPE is not proven ---------
            # OS-49 BUGFIX (final review F-013). F-012 canonicalized the CONTAINER; this
            # gate canonicalizes the trust in the VALUES it carries, and the two are one
            # lesson in two parts: a freshly constructed exact-class `ModelEvidence` does
            # nothing about the fifteen arbitrary objects stored INSIDE it. `ModelEvidence`
            # is a plain frozen dataclass with no field validation, so a driver may put ANY
            # object in ANY field -- and before this gate exactly ONE attested value was
            # exact-type-checked (`observation_method`, N-1a). The other fourteen were
            # validated only by operations the value itself defines: truthiness,
            # `==`/`!=`, tuple equality and `in`. A driver-owned object that answers those
            # cooperatively passed every leg and was then copied BY REFERENCE into the
            # canonical record, so all three authority maps held executable driver code --
            # which the review demonstrated by having `_rebind_model_evidence()` re-enter a
            # `selection_token`'s `__format__` through `request_evidence` and raise, leaving
            # a half-written terminal row after the dispatch had settled.
            #
            # `type(x) is str` / `type(x) is int`, not `isinstance`: a SUBCLASS may override
            # `__eq__`, `__ne__`, `__bool__`, `__hash__`, `__format__`, `__str__` or
            # `__repr__`, which is every operation a later reader performs, so an
            # `isinstance` gate would still hand those readers an object that can lie or
            # raise. `type(x) is int` rejects `bool`, which is INTENDED and is the same rule
            # DESIGN M-14 already applies to `attempt`: `True == 1`, so a bool ordinal
            # silently aliases a real one.
            #
            # Nothing here COERCES. `str(x)` or `safe_text(x)` would turn an arbitrary
            # object into an apparently valid attestation, which is precisely the acceptance
            # this gate exists to refuse -- the rule N-1a stated for one field, now applied
            # to all fifteen.
            #
            # HOISTED to run IMMEDIATELY after the single read of each field and BEFORE any
            # other leg, because placing the checks "where each field is used" would leave
            # the ORDERING hole the review's probe did not even need: the request-presence
            # leg below performs a TRUTHINESS test on `selection_token`, and the state leg
            # performs a MEMBERSHIP test, so a hostile `__bool__` or `__eq__` fired before
            # any type check could run. Between the snapshot above and this gate the values
            # are not touched at all -- building the tuple below binds references and
            # `type(...) is ...` reads the type object, neither of which enters driver code.
            #
            # Fields in DECLARATION order, so the field reported is deterministic. The
            # reason code is per-field because two of them are already locked by tests:
            # `observation_method` is `model_selection_unsupported` (N-1a: leg 2 names a
            # LOCATOR, and a locator that is not a name is unsupported) and `resolved_model`
            # is `model_selection_unverified` (the B3 malformed-type contract). The
            # remaining thirteen take `model_selection_unverified`: an attested field that
            # is not a primitive is not evidence of anything, verified least of all.
            #
            # The diagnostic renders only `safe_type_name(...)` of the offending value, and
            # `refuse()` -> `_model_refusal()` renders the REJECTED driver object through
            # `safe_repr` / `safe_text` (N-1). That is deliberate and is not a hole: a
            # refusal diagnostic describes the value being REJECTED, nothing it renders
            # becomes authority, and a hostile renderer there is normalized by this very
            # boundary (F-001).
            #
            # Reached post-selection, so every failure goes through `refuse()`: current
            # authority for this session is staled, and `_model_role_history` /
            # `_model_session_history` are left standing, exactly as review B2 established.
            for attested_name, attested_value, attested_type, attested_note in (
                ("state", attested_state, str, ATTESTED_PRIMITIVE_NOTE),
                ("requested_model", attested_requested_model, str,
                 ATTESTED_PRIMITIVE_NOTE),
                ("resolved_model", attested_resolved_model, str,
                 ATTESTED_PRIMITIVE_NOTE),
                ("selection_token", attested_selection_token, str,
                 ATTESTED_PRIMITIVE_NOTE),
                ("request_method", attested_request_method, str,
                 ATTESTED_PRIMITIVE_NOTE),
                ("request_stamp", attested_request_stamp, int,
                 ATTESTED_PRIMITIVE_NOTE),
                ("observation_method", attested_observation_method, str,
                 "leg 2 must name the locator that produced the resolved value, and an "
                 "object is not a name"),
                ("observe_stamp", attested_observe_stamp, int,
                 ATTESTED_PRIMITIVE_NOTE),
                ("capability", attested_capability, str, ATTESTED_PRIMITIVE_NOTE),
                ("observed_at_run", attested_observed_at_run, str,
                 ATTESTED_PRIMITIVE_NOTE),
                ("observed_at_task", attested_observed_at_task, str,
                 ATTESTED_PRIMITIVE_NOTE),
                ("observed_at_terminal", attested_observed_at_terminal, str,
                 ATTESTED_PRIMITIVE_NOTE),
                ("observed_at_role", attested_observed_at_role, str,
                 ATTESTED_PRIMITIVE_NOTE),
                ("observed_at_phase", attested_observed_at_phase, str,
                 ATTESTED_PRIMITIVE_NOTE),
                ("observed_at_attempt", attested_observed_at_attempt, int,
                 ATTESTED_PRIMITIVE_NOTE),
            ):
                if type(attested_value) is attested_type:
                    continue
                raise refuse(
                    ATTESTED_FIELD_TYPE_REFUSALS.get(
                        attested_name, MODEL_SELECTION_UNVERIFIED
                    ),
                    f"{attested_name} is {safe_type_name(attested_value)}, not "
                    f"{attested_type.__name__}; {attested_note}",
                )
            if attested_state not in MODEL_EVIDENCE_STATES:
                raise refuse(
                    MODEL_SELECTION_UNVERIFIED,
                    f"state {attested_state!r} is outside the closed vocabulary "
                    f"{MODEL_EVIDENCE_STATES}",
                )
            # ---- leg 1: the REQUEST ------------------------------------------------------
            if not attested_selection_token or not attested_request_method or (
                attested_request_stamp == 0
            ):
                raise refuse(
                    MODEL_SELECTION_REQUEST_ABSENT,
                    "no model selection was attested as REQUESTED for this attempt; "
                    "reading a pre-existing or default model state is not evidence that "
                    "anything asked for it",
                )
            if attested_request_method not in MODEL_SELECTION_REQUEST_METHODS:
                raise refuse(
                    MODEL_SELECTION_UNSUPPORTED,
                    f"request_method {attested_request_method!r} is outside the closed set "
                    f"{MODEL_SELECTION_REQUEST_METHODS}",
                )
            if attested_capability != MODEL_SELECTION_VERIFIED_CAPABILITY:
                raise refuse(
                    MODEL_SELECTION_UNSUPPORTED,
                    "the evidence does not name the "
                    f"{MODEL_SELECTION_VERIFIED_CAPABILITY!r} capability",
                )
            # ---- leg 2's vocabulary, validated HERE and not merely rendered later -------
            # OS-49 BUGFIX (review 5970292670, N-1a). `observation_method` is the only
            # attested field this barrier accepted without looking at it: declared `str`,
            # never validated, and read at just two sites that RENDER it -- the refusal
            # diagnostic above and the `agent_identity_bound` row, the second of which runs
            # from the settled-dispatch logging funnel. So an arbitrary object -- one whose
            # `__bool__` or `__str__` raises -- passed the barrier, was STORED AS AUTHORITY
            # by the accept block at the bottom of this method, and detonated later, after
            # the dispatch it authorized had already settled.
            #
            # Validated beside `request_method` and `capability` because it is the same
            # kind of thing: a closed-vocabulary member naming a mechanism this repository
            # has actually implemented. Leg 2's attestation is as much a claim about HOW
            # the value was obtained as leg 1's is, and a claim naming a locator that does
            # not exist is `model_selection_unsupported` -- the same member, for the same
            # reason, as an unsupported `request_method`.
            #
            # TYPE FIRST, then VALUE -- and OS-49 BUGFIX (final review F-013) MOVED the
            # type half UPWARDS into the type gate, byte-identical reason code and
            # byte-identical diagnostic, because this field was the only one of the fifteen
            # that had such a check and a check sitting at its point of use runs AFTER the
            # legs above it have already touched other unvalidated values. It is not
            # duplicated here: a second `type(...) is not str` test at this line would be
            # unreachable. What remains here is the VALUE half, which is this leg's own
            # business. `type(...) is str` rather than `isinstance` for the reason the gate
            # spells out: the membership test below is `==` against each member, and a `str`
            # SUBCLASS may override `__eq__`.
            #
            # Reached post-selection, so it goes through `refuse()`: current authority for
            # this session is staled, and `_model_role_history` / `_model_session_history`
            # -- the non-drift record -- are deliberately left standing, exactly as review
            # B2 established for every other post-selection refusal.
            if attested_observation_method not in MODEL_SELECTION_OBSERVATION_METHODS:
                raise refuse(
                    MODEL_SELECTION_UNSUPPORTED,
                    f"observation_method {attested_observation_method!r} is outside the "
                    f"closed set {MODEL_SELECTION_OBSERVATION_METHODS}",
                )
            if attested_selection_token != ticket.token:
                raise refuse(
                    MODEL_SELECTION_REQUEST_STALE,
                    "the attested request belongs to another attempt, session or role: its "
                    "token is not the one this barrier minted",
                )
            if attested_observe_stamp == 0:
                raise refuse(
                    MODEL_SELECTION_UNVERIFIED,
                    "a selection was requested and no resolved model was ever observed",
                )
            if (
                attested_request_stamp != expected[0]
                or attested_observe_stamp != expected[1]
                or drawn_to != expected[1]
            ):
                raise refuse(
                    MODEL_SELECTION_REQUEST_STALE,
                    "the two ordinals were not drawn from this ticket exactly twice, in "
                    "order: the only way to obtain one is to call the ticket's stamp(), and "
                    "this harness counted how often it was asked",
                )
            observed_key = (
                attested_observed_at_run,
                attested_observed_at_task,
                attested_observed_at_terminal,
                attested_observed_at_role,
                attested_observed_at_phase,
                attested_observed_at_attempt,
            )
            if observed_key != (
                self.run_id or "",
                task_id,
                terminal,
                role,
                phase,
                attempt,
            ):
                raise refuse(
                    MODEL_SELECTION_REQUEST_STALE,
                    "the evidence was not observed for this (run, task, terminal, role, "
                    "phase, attempt) key",
                )
            if attested_requested_model != requested:
                raise refuse(
                    MODEL_SELECTION_MISMATCH,
                    "the evidence does not echo back the model this routing requested",
                )
            # ---- leg 2: the OBSERVATION's own verdict -------------------------------------
            if attested_state != MODEL_EVIDENCE_VERIFIED:
                raise refuse(
                    {
                        MODEL_EVIDENCE_MISMATCH: MODEL_SELECTION_MISMATCH,
                        MODEL_EVIDENCE_UNVERIFIABLE: MODEL_SELECTION_UNSUPPORTED,
                        MODEL_EVIDENCE_STALE: MODEL_SELECTION_UNVERIFIED,
                        MODEL_EVIDENCE_REQUESTED: MODEL_SELECTION_UNVERIFIED,
                        MODEL_EVIDENCE_NONE: MODEL_SELECTION_UNVERIFIED,
                    }.get(attested_state, MODEL_SELECTION_UNVERIFIED),
                    f"the driver reported state {attested_state!r}; only "
                    f"{MODEL_EVIDENCE_VERIFIED!r} admits a delivery",
                )
            if not attested_resolved_model or not MODEL_TOKEN_PATTERN.fullmatch(
                attested_resolved_model
            ):
                raise refuse(
                    MODEL_SELECTION_UNVERIFIED,
                    f"resolved_model {attested_resolved_model!r} is empty or is not a "
                    "simple model token, so it is not a model identity",
                )
            # OS-49 BUGFIX (review B2). Read off the HISTORY, not off `_model_identity`.
            # `_model_identity` is revocable authority and `_stale_model_evidence()` drops it,
            # so reading it here made the drift baseline disappear exactly when a drift had
            # just been refused -- which is to say, exactly when it was needed. The history is
            # append-only for the run, so the second attempt at a drifted model is refused for
            # the same reason the first one was instead of sliding through on a cleared slate.
            previous_model = self._model_role_history.get((phase, routing_role))
            if previous_model is not None and previous_model != attested_resolved_model:
                raise refuse(
                    MODEL_SELECTION_AMBIGUOUS,
                    "an earlier accepted attempt for this (phase, role) resolved to "
                    f"{previous_model!r}; a model that silently changed between "
                    "rounds does not merely get logged, the dispatch does not happen",
                )
            # ---- (j) the effective-identity rule, on RESOLVED values ----------------------
            # The half the declaration-time gate structurally cannot see: two DISTINCT
            # declared tokens can resolve to one model, and only a resolved-value comparison
            # refuses that.
            #
            # OS-49 iteration 2 (review F-001). This check used to run only when a
            # counterpart record HAPPENED to exist, which made it vacuous on the Worker --
            # whose dispatch precedes its Reviewer session -- so a same-command pair's Worker
            # was DELIVERED and only the Reviewer, arriving second, was refused. A Worker that
            # has already run was admitted without independence ever being positively
            # established, which is precisely what the ticket forbids. The fix is not to
            # delete the check but to move the ADMISSION: on a SAME-COMMAND pair the
            # counterpart's verified evidence must ALREADY EXIST at the first delivery of
            # EITHER role, and its absence is a refusal with its own name.
            #
            # How a caller satisfies it: create/attach BOTH sessions, call the public
            # `verify_model_identity()` pre-pass for each role -- which runs legs (a)-(i) and
            # records the identity but DELIVERS NOTHING -- and only then dispatch. A caller
            # that cannot create both sessions up front cannot route a same-command
            # model-aware pair at all, which is the fail-closed outcome, not a gap.
            #
            # DISTINCT-command pairs are untouched: row 1 of the rule makes them independent
            # on the commands alone, so no counterpart evidence is required and the
            # pre-OS-49 lifecycle stands. The Final Reviewer is deliberately outside this
            # rule and has no counterpart to look up.
            # RE-READ, not the pre-selection values reused: `select_and_verify()` is
            # arbitrary driver code that ran in between, and the honest assumption about
            # arbitrary code is that harness state may have moved under it. The run-scope
            # filter (review F-002) lives in `_counterpart_model_identity()` so this read and
            # the pre-selection one cannot disagree about which records count.
            counterpart_role, counterpart = self._counterpart_model_identity(
                phase=phase, routing_role=routing_role
            )
            if counterpart_role is not None:
                counterpart_entry = self.agent_routing.for_role(phase, counterpart_role)
                # Scoped exactly as Gate A's pair check is: to a counterpart that is REQUIRED
                # and resolved. At LOW risk the Reviewer entry exists but is optional and no
                # Reviewer is ever dispatched, so there is no pair to admit -- and the
                # repository's standing rule is that a role nobody dispatches must not fail a
                # run (the same reason the PATH check is scoped to required roles). An
                # unresolved required role is validate_required_roles()' business.
                # Read through the ONE predicate `pair_admission_required()` reads, so the
                # hoisted copy, this backstop and the public predicate cannot disagree.
                same_command = self._same_command_pair(
                    phase=phase, command=command, counterpart_role=counterpart_role
                )
                if counterpart is None:
                    # OS-49 iteration 2 (final review R2), retained as the POST-selection
                    # backstop after the identical condition was hoisted above the driver
                    # call. The pre-selection copy is the one that fires for every ordinary
                    # caller, and it is the one that matters, because it fires before the
                    # session can be switched.
                    #
                    # This copy is not dead code, for the same reason the session check's
                    # backstop is not: `select_and_verify()` is arbitrary driver code running
                    # between the two reads, and a driver that re-enters this method -- or
                    # otherwise writes `_model_identity` -- can DELETE or run-scope-invalidate
                    # a counterpart record that existed at the pre-check, which the
                    # pre-selection read could not have anticipated. It is also what stops a
                    # future reordering from silently reopening the window: delete the hoist
                    # and this still refuses, just later and with the session already mutated.
                    if require_pair_admission and same_command:
                        raise refuse(
                            MODEL_SELECTION_PAIR_UNADMITTED,
                            f"the {counterpart_role} of phase {phase!r} shares this "
                            f"command {command!r} and has no positively verified model "
                            "evidence yet, so Worker/Reviewer independence rests on nothing "
                            "but two declared tokens -- which can alias onto one model. "
                            "Verify both effective identities with verify_model_identity() "
                            "before delivering either",
                        )
                elif counterpart.observed_at_terminal == terminal:
                    # OS-49 BUGFIX (review M5), retained as the POST-selection backstop after
                    # review B1 hoisted the same comparison above the driver call. The
                    # pre-selection copy is the one that fires for every ordinary caller, and
                    # it is the one that matters, because it fires before the session can be
                    # switched.
                    #
                    # This copy is not dead code and is not redundant. `select_and_verify()`
                    # is arbitrary driver code executing between the two reads, and a driver
                    # that re-enters `verify_model_identity()` -- or otherwise writes
                    # `_model_identity` -- can create a counterpart record naming THIS
                    # terminal in that window, which the pre-selection read could not have
                    # seen. It is also the guard that stops a future reordering of this method
                    # from silently reopening B1: delete the hoist and this still refuses,
                    # just later and with the session already mutated.
                    #
                    # Reached post-selection, it therefore goes through `refuse()` and STALES
                    # the session, which is correct for exactly the reason the hoist exists:
                    # here the switch really did happen.
                    raise refuse(
                        REASON_WORKER_REVIEWER_MUST_DIFFER,
                        f"the {counterpart_role} of phase {phase!r} was positively verified "
                        f"on this very session {terminal!r} (resolved "
                        f"{counterpart.resolved_model!r}); one physical session cannot be "
                        "both sides of a pair, so its independence is not established no "
                        "matter which models the two verifications resolved to",
                    )
                elif counterpart_entry is not None:
                    independent, reason = effective_identity_independent(
                        (command, attested_resolved_model, MODEL_EVIDENCE_VERIFIED),
                        (
                            counterpart_entry.command,
                            counterpart.resolved_model,
                            MODEL_EVIDENCE_VERIFIED,
                        ),
                    )
                    if not independent:
                        raise refuse(
                            reason or REASON_WORKER_REVIEWER_MUST_DIFFER,
                            f"the {counterpart_role} of phase {phase!r} already resolved to "
                            f"command {counterpart_entry.command!r} model "
                            f"{counterpart.resolved_model!r}; two distinct declared tokens "
                            "that resolve to one model are not two agents",
                        )
            # ---- (k) the SESSION's own identity history (OS-49 BUGFIX, review M2/M5) ------
            # Leg (i) above compares against the last record for this (phase, ROLE); this leg
            # compares against the last record for this physical TERMINAL. They are different
            # keys answering different questions, and the gap between them was M2: one session
            # reused from IMPLEMENTATION into TEST has a DIFFERENT (phase, role) key, so leg
            # (i) missed entirely and the only comparison left was the requested ALIAS -- which
            # is a declaration, and two attempts declaring alias-X prove nothing about whether
            # the provider resolved it to model-A both times.
            #
            # Ordered AFTER the counterpart block deliberately: when one terminal has been
            # verified for both roles, the fact that matters is the PAIR violation above, which
            # has its own name and its own invariant. Reaching here means the session is being
            # re-verified for the SAME routing role, where a changed resolved model is exactly
            # `model_selection_ambiguous` -- the same name leg (i) uses for the same fact.
            #
            # OS-49 BUGFIX (review B2). Read off `_model_session_history`, not off
            # `_model_session_identity`. This is THE B2 site: `_stale_model_evidence()` popped
            # the authoritative session record, which was the only drift baseline this leg had,
            # so the sequence `alias-X -> model-A accepted`, `alias-X -> model-B refused`,
            # `alias-X -> model-B retried` found nothing to compare against on the third
            # attempt and admitted the very drift the second attempt was refused for. The
            # history survives staling, so the retry is refused too.
            #
            # This is not a permanent stall. A retry that resolves BACK to the session's
            # baseline still matches history and is still accepted -- that is RECOVERY, and it
            # is what re-establishes authority. What is refused is the other case: a session
            # whose resolved model CHANGED is, by OS-49's own stated principle, not the agent
            # that was verified, so it cannot be re-badged. Its replacement is a new session,
            # which every caller can create and which costs a `terminal create`.
            #
            # OS-49 BUGFIX (review 5970292670, N-3). The replacement session is NOT a way to
            # change the role's model, and the superseded wording did not say so. A new
            # terminal is unknown to THIS leg's `_model_session_history` and so clears THIS
            # leg; it is not known to leg (i) either way, because leg (i) is keyed on
            # (phase, routing role) and that key is unchanged by moving sessions. So inside
            # one run the replacement must still resolve to the ROLE's established baseline,
            # and a replacement that resolves to anything else is refused by leg (i) with the
            # same `model_selection_ambiguous` name. Changing a role's resolved model is a
            # new RUN, not a new session.
            session_previous = self._model_session_history.get(terminal)
            if session_previous is not None:
                previous_role, previous_phase, previous_model = session_previous
                if previous_model != attested_resolved_model:
                    raise refuse(
                        MODEL_SELECTION_AMBIGUOUS,
                        f"session {terminal!r} was already positively verified as "
                        f"{previous_role!r} of phase {previous_phase!r} resolving to "
                        f"{previous_model!r}; a session whose resolved "
                        "model changed between attempts is not the agent that was verified, "
                        "and an equal requested alias is a declaration rather than evidence "
                        "that the provider resolved it the same way twice",
                    )
            # Accepted. Recorded ONLY here, so no refused attempt can leave a trace that a
            # later round or a provenance row would read as earned.
            # OS-49 BUGFIX (final review F-012). The authority is the CANONICAL record,
            # built only from the locals the legs above validated -- never the driver's
            # object. Exact-class and frozen, and -- OS-49 BUGFIX (final review F-013) --
            # carrying only values the type gate proved to be exact `str` / `int`, so every
            # later reader (the `agent_identity_bound` row, reuse condition 9, the
            # provenance rows, `_rebind_model_evidence()`'s `request_evidence`, the refusal
            # diagnostics of a LATER attempt) reads the validated values back and cannot
            # re-enter driver code. BOTH halves are load-bearing for that last sentence: an
            # exact-class wrapper around fifteen driver-owned objects was F-013, and
            # exact-typed values inside the driver's own object would still be a
            # time-of-check/time-of-use split. Constructed here rather than at the top
            # because it must carry what was VALIDATED, and that is only known once every
            # leg has passed.
            canonical = ModelEvidence(
                state=attested_state,
                requested_model=attested_requested_model,
                resolved_model=attested_resolved_model,
                selection_token=attested_selection_token,
                request_method=attested_request_method,
                request_stamp=attested_request_stamp,
                observation_method=attested_observation_method,
                observe_stamp=attested_observe_stamp,
                capability=attested_capability,
                observed_at_run=attested_observed_at_run,
                observed_at_task=attested_observed_at_task,
                observed_at_terminal=attested_observed_at_terminal,
                observed_at_role=attested_observed_at_role,
                observed_at_phase=attested_observed_at_phase,
                observed_at_attempt=attested_observed_at_attempt,
            )
            self._model_identity[(phase, routing_role)] = canonical
            self._model_pending_evidence[terminal] = canonical
            self._model_session_identity[terminal] = (routing_role, phase, canonical)
            # OS-49 BUGFIX (review B2). The HISTORY half of the same acceptance, written in the
            # same statement group so authority can never exist without the history that
            # explains it. Deliberately NOT rolled back by `_restore_model_evidence()`: that
            # rollback exists because an accepted barrier must not authorize a delivery that
            # never completed, and it is about AUTHORITY. The selection itself did happen, so
            # the session really is on this model and the history row is true whether or not
            # the delivery that followed it survived.
            self._model_role_history[(phase, routing_role)] = attested_resolved_model
            self._model_session_history[terminal] = (
                routing_role, phase, attested_resolved_model
            )
            row = self._terminals.get(terminal)
            if row is not None:
                row["resolved_model"] = attested_resolved_model
                row["model_state"] = attested_state
        except BaseException as exc:                   # noqa: BLE001 - re-raised below
            # The invariant, in two statements. Stale FIRST -- unconditionally, before
            # anything is decided about what kind of failure this is -- then decide how the
            # failure should travel.
            self._stale_model_evidence(terminal)
            if isinstance(exc, OrcaRuntimeError) or not isinstance(exc, Exception):
                # A refusal this method built (every `raise refuse(...)` above, which has
                # already staled -- `_stale_model_evidence()` is idempotent), or control
                # flow the vocabulary does not speak for. Both propagate untouched, so no
                # existing refusal reason, message or exception type moves.
                raise
            # Anything else is MALFORMED EVIDENCE: the driver returned a shape this
            # harness's own validation could not evaluate. That is a failed selection, not
            # a harness defect, so it is normalized into the closed vocabulary for exactly
            # the reason M3 normalized a raising driver -- a caller cannot branch on a
            # `TypeError`. `model_selection_unverified` is the right member: a selection was
            # requested and no resolved model was ever positively verified.
            #
            # `evidence` is deliberately NOT passed to the refusal. Rendering it reads every
            # field, which is the operation that just raised, and a diagnostic must not be
            # able to replace the failure it is diagnosing. The type name and the exception
            # text carry the diagnosis instead.
            raise self._model_refusal(
                MODEL_SELECTION_UNVERIFIED,
                role=role,
                phase=phase,
                attempt=attempt,
                command=command,
                requested_model=requested,
                ticket=ticket,
                expected_window=expected,
                drawn_to=drawn_to,
                detail="post-selection validation of the driver's evidence raised "
                f"{safe_exception_text(exc)}; the evidence is malformed, so no resolved "
                "model was positively verified for this attempt",
            ) from exc

    def start_worker(
        self,
        task_id: str,
        terminal: str,
        spec: str,
        *,
        role: str = "",
        phase: str = "",
        attempt: int = 0,
    ) -> tuple[str, bool]:
        """OS-49: the three keyword-only identity parameters are what the pre-delivery
        barrier keys this attempt's model evidence to. They have defaults so all nine
        existing unpack sites bind unchanged and the return type stays
        `tuple[str, bool]`, but OMITTING them on a model-aware run is a REFUSAL, not a
        skip -- see `_gate_b_model_identity`.
        """
        # GATE B, the first statement, deliberately ahead of the own-handle refusal and
        # therefore ahead of wait_for_tui_idle(), the rung-3 delivery and the rung-4
        # delivery. It does not displace the OS-29 B1 guard (which runs earlier, in both
        # initiators) and does not move the OS-41 acknowledgement gate (which runs
        # later, on the receipt).
        snapshot = self._gate_b_model_identity(
            task_id=task_id,
            terminal=terminal,
            role=role,
            phase=phase,
            attempt=attempt,
        )
        # OS-49 BUGFIX (review N1). Everything below is the DELIVERY, and an accepted
        # model identity must not outlive it. The barrier records on acceptance -- which
        # it must, because the pair-admission and reuse reads happen against that record
        # -- but every refusal and failure from here on means no task was delivered on
        # that identity, so the record is rolled back to exactly its pre-barrier state and
        # the exception continues unchanged. `BaseException`, not `Exception`: a
        # KeyboardInterrupt between the barrier and the delivery leaves the same false
        # evidence behind, and the handler re-raises rather than absorbing anything.
        try:
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
        except BaseException:
            self._restore_model_evidence(snapshot)
            raise

    def delivery_obligations(self) -> dict[str, str]:
        """Every open delivery obligation this Coordinator holds, by kind.

        OS-44 (BUGFIX-I3-MAJOR-1). The previous round asked one question -- "is this row
        recovered?" -- and EXCLUDED every recovered row from the pending check, which is
        how an incomplete acknowledgement disappeared instead of being closed. The
        exclusion was not arbitrary: counting a predecessor's awaiting-redelivery
        obligation refuses to arm the very waiter that redelivery has to arrive on, a
        permanent stall. Both rules are right about different rows, so the rows are now
        told apart by HOW FAR the predecessor got, in one pure classifier
        (`quiescence.delivery_obligation`) that this class and the turn-end CLI share.
        """
        return {
            delivery_id: obligation
            for delivery_id, row in self._deliveries.items()
            for obligation in (quiescence.delivery_obligation(row),)
            if obligation != quiescence.OBLIGATION_NONE
        }

    def unacknowledged_deliveries(self) -> tuple[str, ...]:
        """Every delivery whose acknowledgement blocks arming a waiter or ending a turn.

        Read-only, and the single source both the waiter gate and the turn-end
        quiescence self-check read, so "may I arm the next waiter?" and "may this turn
        end?" can never disagree about which deliveries are outstanding.

        Two obligation kinds stand aside, and they are named
        (`quiescence.REDELIVERY_RESOLVED_OBLIGATIONS`) rather than merely "recovered":
        the rows a predecessor left without ever issuing the wire ack. Orca replays such
        a delivery until it is acknowledged, so the redelivery is what discharges them --
        and it can only arrive on the waiter that counting them would refuse to arm.
        They are not hidden: `delivery_obligations()` still reports them, the turn-end
        boundary reports them as runnable work rather than as rest, and a claimed-but-
        unsettled one still fails closed when its redelivery is settled.

        Every other open obligation blocks, including a predecessor's settled-but-
        unacknowledged row -- the post-wire-ack window, which no redelivery is
        guaranteed to close. Those are closed by
        `reconcile_recovered_acknowledgements()`, never by being excluded.
        """
        return tuple(
            delivery_id
            for delivery_id, obligation in self.delivery_obligations().items()
            if obligation not in quiescence.REDELIVERY_RESOLVED_OBLIGATIONS
        )

    def _assert_every_delivery_acknowledged(self, action: str) -> None:
        """OS-44 ordering gate. Fail closed before arming another waiter.

        The acceptance criterion this enforces is stated as an ordering, so it is
        enforced as one: state and settlement are reflected first, the delivery is
        acknowledged second, and only then may a new waiter exist. A Coordinator that
        arms the next `check --wait` with a processed-but-unacknowledged delivery
        outstanding gets that delivery replayed into the new waiter, which is how a
        PLAN waiter woke on an ANALYSIS re-review result.
        """
        pending = self.unacknowledged_deliveries()
        if not pending:
            return
        detail = (
            f"refusing to {action}: delivery {', '.join(pending)} was processed and "
            "not acknowledged; acknowledge it first or the next waiter wakes on the "
            "replay"
        )
        self._audit_coordinator(
            run_logging.EVENT_QUIESCENCE_VIOLATION,
            reason_code=quiescence.QUIESCENCE_UNACKNOWLEDGED_DELIVERY,
            detail=detail,
            delivery_id=pending[0],
        )
        raise OrcaRuntimeError(detail)

    def _check(self) -> dict[str, Any]:
        assert self.run_owner
        # OS-44 (BUGFIX-I1-G1-2). Restart recovery happens HERE, on the production wait
        # path, and before the gate below -- not in a helper a caller could forget. Any
        # route by which this process came to hold an existing run id (a successor
        # binding through resume_run(), or any future one) passes through this method
        # before it can arm a `check --wait`, so a redelivered batch is judged against
        # the previous process's ledger rather than against an empty one. It runs above
        # the acknowledgement gate because a recovered row can itself be outstanding.
        self._restore_delivery_ledger_once()
        # The gate is the FIRST gate, above the command, for the same reason
        # claim_settlement() is settle_attempt()'s: a gate after the action it guards
        # is a report, not a gate.
        self._assert_every_delivery_acknowledged("arm the next delivery wait")
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

    def _record_delivery_processed(
        self,
        delivery_id: str,
        *,
        task_id: str = "",
        dispatch_id: str = "",
        message_id: str = "",
    ) -> dict[str, Any]:
        """Mark a delivery consumed, BEFORE the caller acts on what it contained.

        Written first on purpose. The row is what makes the ack obligation visible to
        the waiter gate and to the turn-end self-check, so a Coordinator that dies,
        returns or raises between consuming a delivery and acknowledging it leaves an
        outstanding obligation rather than a silent gap.

        OS-44 (BUGFIX-I2-G1-1). Opening the row and COMMITTING the progress transition
        are two different things, and they are ordered differently for that reason. The
        row is opened first because an unopened row is an invisible obligation; the
        transition to ``processed`` commits only after the durable record exists,
        because a transition that could not be recorded has not happened. An
        unpublished row stays in the obligation set (its state is not
        ``acknowledged``), so the failure is fail-closed in both directions at once.
        """
        row = self._deliveries.setdefault(
            delivery_id,
            {
                "delivery_id": delivery_id,
                "task_id": task_id,
                "dispatch_id": dispatch_id,
                "message_id": message_id,
                DELIVERY_STATE_FIELD: "",
                "replays": 0,
                "ack_attempts": 0,
                "ack_error": "",
                # How far this delivery's settlement got. Both flip through
                # _record_delivery_settlement(), which publishes the matching audit
                # record BEFORE the flag flips, so the in-memory row can never claim
                # progress the artifact a successor recovers from does not carry.
                DELIVERY_SETTLEMENT_CLAIMED_FIELD: False,
                DELIVERY_SETTLED_FIELD: False,
                DELIVERY_ACK_INTENT_FIELD: False,
                DELIVERY_RECOVERED_FIELD: False,
            },
        )
        self._audit_coordinator(
            run_logging.EVENT_DELIVERY_PROCESSED,
            delivery_id=delivery_id,
            task_id=task_id or row.get("task_id", ""),
            dispatch_id=dispatch_id or row.get("dispatch_id", ""),
            message_id=message_id or row.get("message_id", ""),
        )
        # Durably recorded above; only now does the in-memory transition commit.
        row[DELIVERY_STATE_FIELD] = DELIVERY_STATE_PROCESSED
        # THIS process has now consumed it, so the acknowledgement is its own
        # obligation even if the row arrived here through restart recovery.
        row[DELIVERY_RECOVERED_FIELD] = False
        return row

    def _record_delivery_settlement(
        self, delivery_id: str, field: str, event: str, **fields: Any
    ) -> None:
        """Record how far this delivery's settlement got: DURABLY, then in memory.

        OS-44 (BUGFIX-I1-G1-1). The two calls to this method are the boundaries a crash
        has to be recoverable across: the claim, published before the settlement path's
        first Orca command, and the settled record, published after state and settlement
        are fully reflected and before the acknowledgement. The audit write is
        fail-closed (see _audit_coordinator), so a boundary that could not be recorded
        stops the run instead of leaving a successor to guess.

        OS-44 (BUGFIX-I2-G1-1). The order inside this method is the whole point and is
        not an implementation detail: the durable record is published FIRST and the
        in-memory flag flips only if that publication succeeded. A transition that
        cannot be durably recorded has not happened, so memory must never run ahead of
        the artifact. Publishing second -- which is what iteration 2 did -- left the row
        saying "settled" while the audit said only "claimed", and every later reader of
        that row (the STEP 0 discharge, the waiter gate, a successor's recovery) then
        decided from a fact no artifact supported.
        """
        self._audit_coordinator(event, delivery_id=delivery_id, **fields)
        row = self._deliveries.get(delivery_id)
        if row is not None:
            row[field] = True

    def _ack(self, delivery_id: str) -> None:
        """Acknowledge one delivery, with a bounded retry and a fail-closed edge.

        Every attempt, the success and the exhaustion are recorded in the run's
        append-only Coordinator audit, so an ack failure is a named reason in an
        artifact rather than an inference from a missing row.
        """
        assert self.run_owner
        row = self._deliveries.get(delivery_id)
        if row is None:
            # A delivery acknowledged without having been recorded as processed --
            # the non-matching batches wait_for_done() discards, and the runtime exit
            # report checkpoint. Record it now so the audit still carries a
            # processed/acknowledged pair for every acknowledgement this run issues.
            row = self._record_delivery_processed(delivery_id)
        # OS-44 (BUGFIX-I3-MAJOR-1). The ack INTENT is published before the wire
        # command, not after it. Orca accepts `--ack` and consumes the delivery before
        # this process can publish anything, so a process that dies inside the command
        # leaves an audit that -- without this record -- ends at `delivery_settled` and
        # cannot say whether the delivery is still in the mailbox awaiting replay or has
        # already been consumed with its acknowledgement outcome unrecorded. A successor
        # that cannot tell those apart either re-drives a consumed delivery or silently
        # drops the obligation. Published once, before the first attempt: the retries
        # below are the same intent, not new ones.
        if not row.get(DELIVERY_ACK_INTENT_FIELD):
            self._record_delivery_settlement(
                delivery_id,
                DELIVERY_ACK_INTENT_FIELD,
                run_logging.EVENT_DELIVERY_ACK_INTENT,
                task_id=row.get("task_id", ""),
                dispatch_id=row.get("dispatch_id", ""),
            )
            row[DELIVERY_STATE_FIELD] = DELIVERY_STATE_ACK_INTENT
        last_error: Any = None
        for attempt in range(1, ACK_MAX_ATTEMPTS + 1):
            response = self.call(
                "orchestration",
                "check",
                "--terminal",
                self.run_owner,
                "--ack",
                delivery_id,
                allow_error=True,
            )
            if response.get("ok"):
                # OS-44 (BUGFIX-I2-G1-1). Durable first here too. R8 requires the
                # acknowledgement outcome to be IN the audit; publishing it after the
                # in-memory transition meant a failed write left the row saying
                # "acknowledged" over an audit that ended at `delivery_settled`, and
                # the run then carried on past the one outcome it owed. If the record
                # cannot be published the transition does not commit: the delivery
                # stays an outstanding obligation, the waiter gate and the turn-end
                # self-check both refuse, and _audit_coordinator raises. A re-entry
                # re-publishes it -- the wire ack is idempotent and STEP 0 discharges
                # an already-finalized dispatch -- so the outcome is recorded on the
                # retry rather than permanently omitted.
                self._audit_coordinator(
                    run_logging.EVENT_DELIVERY_ACKNOWLEDGED,
                    delivery_id=delivery_id,
                    task_id=row.get("task_id", ""),
                    dispatch_id=row.get("dispatch_id", ""),
                    attempts=attempt,
                )
                row[DELIVERY_STATE_FIELD] = DELIVERY_STATE_ACKNOWLEDGED
                row["ack_attempts"] = attempt
                row["ack_error"] = ""
                return
            last_error = response.get("error")
            row["ack_attempts"] = attempt
            self._audit_coordinator(
                run_logging.EVENT_DELIVERY_ACK_RETRY,
                delivery_id=delivery_id,
                task_id=row.get("task_id", ""),
                dispatch_id=row.get("dispatch_id", ""),
                attempts=attempt,
                detail=f"ack attempt {attempt} of {ACK_MAX_ATTEMPTS} failed: {last_error}",
            )
        row[DELIVERY_STATE_FIELD] = DELIVERY_STATE_ACK_FAILED
        row["ack_error"] = str(last_error)
        detail = (
            f"delivery {delivery_id} could not be acknowledged after "
            f"{ACK_MAX_ATTEMPTS} attempts (last error: {last_error}); the Coordinator "
            "fails closed rather than arm a waiter that would wake on the replay"
        )
        self._audit_coordinator(
            run_logging.EVENT_DELIVERY_ACK_FAILED,
            delivery_id=delivery_id,
            task_id=row.get("task_id", ""),
            dispatch_id=row.get("dispatch_id", ""),
            attempts=ACK_MAX_ATTEMPTS,
            detail=detail,
        )
        raise OrcaRuntimeError(detail)

    def reconcile_recovered_acknowledgements(self) -> tuple[str, ...]:
        """Close every acknowledgement a PREDECESSOR process left open.  Deterministic.

        OS-44 (BUGFIX-I3-MAJOR-1).  This is the restart half of the post-wire-ack crash
        window.  A predecessor that recorded `delivery_settled` -- or that got as far as
        `delivery_ack_intent` -- and then died left an acknowledgement outcome this run
        owes and cannot infer: Orca may already have consumed the delivery, in which case
        no redelivery is coming and waiting for one is waiting forever.

        So the successor closes it instead of waiting -- when, and only when, it can
        establish an outcome.  For each such row it re-issues the idempotent wire
        acknowledgement and publishes ONE terminal `delivery_ack_reconciled` record for
        either observation that settles the question: the runtime accepted the ack
        (which includes the already-consumed case, since a duplicate ack succeeds), or
        it answered with a documented `ACK_NOT_OUTSTANDING_ERROR_CODES` code, meaning it
        holds no such delivery for this Run.  Any other failure establishes neither, so
        the obligation is left OPEN and the reconciliation fails closed
        (`_refuse_ack_reconciliation`) rather than inferring consumption from an error
        it could not classify.

        Repeating no lifecycle action is what makes this safe, and it is a property of
        the rows it selects rather than of care taken here: every one of them already
        carries a durable `settled` or `ack_intent` record, so
        `quiescence.delivery_disposition` classifies it as a replay, and the
        finalize-once ledger refuses a second settlement independently.  If the runtime
        did still hold the delivery and this ack somehow does not land, the redelivery
        arrives on the normal replay path and is acknowledged again with zero lifecycle
        action -- correct either way, dependent on redelivery in neither.

        Returns the deliveries it closed, in the order it closed them.
        """
        reconciled: list[str] = []
        for delivery_id, obligation in sorted(self.delivery_obligations().items()):
            if obligation != quiescence.OBLIGATION_ACK_RECONCILE:
                continue
            self._reconcile_ack(delivery_id)
            reconciled.append(delivery_id)
        return tuple(reconciled)

    def _reconcile_ack(self, delivery_id: str) -> str:
        """Re-issue one inherited acknowledgement and record its terminal outcome.

        Exactly two observations are terminal, and neither is inferred from a failure
        this method could not classify:

        * the runtime ACCEPTED the acknowledgement (`ok`) -- which includes the case
          where it had already consumed the delivery, because a re-ack of an
          already-acknowledged delivery succeeds as a duplicate; or
        * it refused with an error code in `ACK_NOT_OUTSTANDING_ERROR_CODES`, which is
          the runtime stating that it holds no such delivery for this Run.

        Anything else -- transport, runtime, permission, unknown, transient, a fenced
        consumer -- proves nothing about whether Orca consumed the delivery, so the
        obligation is PRESERVED and this method fails closed. The previous round turned
        every exhausted retry into `reconciled_not_outstanding` and dropped the
        obligation, which let a successor proceed on a reconciliation that had
        established neither acceptance nor absence.
        """
        assert self.run_owner
        row = self._deliveries[delivery_id]
        last_error: Any = None
        accepted = False
        not_outstanding = False
        for attempt in range(1, ACK_MAX_ATTEMPTS + 1):
            response = self.call(
                "orchestration",
                "check",
                "--terminal",
                self.run_owner,
                "--ack",
                delivery_id,
                allow_error=True,
            )
            if response.get("ok"):
                accepted = True
                break
            last_error = response.get("error")
            code = (last_error or {}).get("code") if isinstance(last_error, dict) else None
            if code in ACK_NOT_OUTSTANDING_ERROR_CODES:
                # Authoritative absence. Retrying it would only repeat the same answer.
                not_outstanding = True
                break
        if not accepted and not not_outstanding:
            return self._refuse_ack_reconciliation(delivery_id, row, last_error)
        outcome = (
            ACK_RECONCILED_ACKNOWLEDGED if accepted else ACK_RECONCILED_NOT_OUTSTANDING
        )
        detail = (
            f"delivery {delivery_id} was settled or ack-issued by a previous process "
            "and its acknowledgement outcome was never recorded; the successor "
            + (
                "re-issued the acknowledgement and the runtime accepted it"
                if accepted
                else "re-issued the acknowledgement and the runtime answered that it "
                f"holds no such delivery for this run ({last_error}), which is itself "
                "the terminal outcome"
            )
        )
        # Durable first, as every other delivery progress transition in this class is:
        # a reconciliation that cannot be recorded has not happened, and the row stays
        # an open obligation that blocks the next waiter and the turn end.
        self._audit_coordinator(
            run_logging.EVENT_DELIVERY_ACK_RECONCILED,
            delivery_id=delivery_id,
            task_id=row.get("task_id", ""),
            dispatch_id=row.get("dispatch_id", ""),
            reason_code=outcome,
            detail=detail,
        )
        row[DELIVERY_STATE_FIELD] = DELIVERY_STATE_ACK_RECONCILED
        row[DELIVERY_ACK_INTENT_FIELD] = True
        return outcome

    def _refuse_ack_reconciliation(
        self, delivery_id: str, row: dict[str, Any], last_error: Any
    ) -> str:
        """The reconciliation established nothing, so it closes nothing. Fail closed.

        The row keeps its open obligation -- `delivery_obligation` still classifies it as
        `ack_reconcile` -- so `unacknowledged_deliveries()`, the waiter gate and the
        turn-end boundary all keep refusing until a later attempt reaches one of the two
        terminal observations. The failure is published first, for the same reason every
        other transition in this class is: a successor has to see the attempt.
        """
        detail = (
            f"delivery {delivery_id} was settled or ack-issued by a previous process and "
            f"its acknowledgement could not be reconciled after {ACK_MAX_ATTEMPTS} "
            f"attempts (last error: {last_error}); that failure is not evidence the "
            "runtime consumed the delivery, so the obligation stays open and the "
            "Coordinator fails closed rather than closing it on an unclassified error"
        )
        self._audit_coordinator(
            run_logging.EVENT_DELIVERY_ACK_FAILED,
            delivery_id=delivery_id,
            task_id=row.get("task_id", ""),
            dispatch_id=row.get("dispatch_id", ""),
            attempts=ACK_MAX_ATTEMPTS,
            reason_code=ACK_RECONCILE_UNRESOLVED,
            detail=detail,
        )
        row[DELIVERY_STATE_FIELD] = DELIVERY_STATE_ACK_FAILED
        raise OrcaRuntimeError(detail)

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

    def wait_for_done(self, dispatch_id: str, task_id: str) -> tuple[dict[str, Any], str]:
        """Wait until THIS Dispatch's `worker_done` arrives, and adopt only that.

        OS-44. Three properties this loop is required to hold, each of which the
        recorded `run_c2166e75bb02` failure broke:

        1. **Provenance before adoption.** A `worker_done` is adopted as this waiter's
           result only when it names BOTH the expected Dispatch and the expected Task.
           The loop used to compare `dispatchId` alone, and anything that failed that
           one comparison was acknowledged away at the bottom of the loop with no
           record of what had been discarded. A message that does not belong to this
           waiter is now recorded as a mismatch and never becomes `done`.
        2. **Replay is not a result.** A delivery this run has already processed and
           acknowledged is acknowledged again and produces no lifecycle action at all
           -- no settlement, no release, no artifact, no dispatch, no iteration
           consumption -- and the loop keeps waiting for a real one. A replay is
           bounded: past `DELIVERY_REPLAY_LIMIT` the Coordinator fails closed rather
           than spin against a runtime that is not consuming the acknowledgement.
        3. **The consumption is recorded before the return.** The matched delivery is
           marked processed HERE, not at the settlement that follows, so the ack
           obligation exists from the moment the delivery is consumed. Anything that
           ends the turn, raises, or returns between this method and the settlement's
           ack now leaves a visible outstanding obligation that both
           `_assert_every_delivery_acknowledged` and `verify_quiescence` refuse.

        `task_id` is required rather than optional. The pre-mutation settlement gate
        already compares both identities, but it runs after adoption -- which is why an
        optional expected-Task would leave exactly the gap this ticket exists to close.
        """
        while True:
            delivery = self._check()
            if delivery.get("timedOut") or not delivery.get("messages"):
                raise OrcaRuntimeError(f"timed out waiting for Dispatch {dispatch_id}")
            delivery_id = delivery["deliveryId"]
            disposition, reason = quiescence.delivery_disposition(
                delivery_id, self._deliveries
            )
            if disposition == quiescence.DELIVERY_REPLAY_EXHAUSTED:
                self._audit_coordinator(
                    run_logging.EVENT_DELIVERY_REPLAYED,
                    delivery_id=delivery_id,
                    dispatch_id=dispatch_id,
                    task_id=task_id,
                    replays=int(self._deliveries[delivery_id].get("replays") or 0) + 1,
                    reason_code=quiescence.DELIVERY_REPLAY_EXHAUSTED,
                    detail=reason,
                )
                raise OrcaRuntimeError(reason)
            if disposition == quiescence.DELIVERY_RECOVER:
                # A previous process claimed this delivery's settlement and never
                # recorded finishing it. A claim carries no proof of how many lifecycle
                # commands already went out, so repeating one could DUPLICATE a
                # release; the run stops here and is recovered explicitly, exactly as
                # claim_settlement() does for an in_progress row inside one process.
                self._audit_coordinator(
                    run_logging.EVENT_DELIVERY_RECOVERY,
                    delivery_id=delivery_id,
                    dispatch_id=dispatch_id,
                    task_id=task_id,
                    reason_code=quiescence.DELIVERY_RECOVER,
                    detail=reason,
                )
                raise OrcaRuntimeError(reason)
            if disposition == quiescence.DELIVERY_RESUME:
                # The other side of the same restart boundary: consumed, nothing
                # claimed, nothing settled, never acknowledged. NOTHING was mutated for
                # it, so the redelivery is how the result gets processed rather than
                # lost -- discarding it as a replay here is precisely the loss this
                # ticket forbids. It falls through into the ordinary message scan
                # below; exactly-once is still the settlement ledger's job.
                self._audit_coordinator(
                    run_logging.EVENT_DELIVERY_RECOVERY,
                    delivery_id=delivery_id,
                    dispatch_id=dispatch_id,
                    task_id=task_id,
                    reason_code=quiescence.DELIVERY_RESUME,
                    detail=reason,
                )
            if disposition == quiescence.DELIVERY_REPLAY:
                # Zero lifecycle action, by construction: this branch acknowledges and
                # loops. It never inspects the messages, so no replayed `worker_done`
                # can reach `done` and no replayed question can be replied to twice.
                row = self._deliveries[delivery_id]
                row["replays"] = int(row.get("replays") or 0) + 1
                self._audit_coordinator(
                    run_logging.EVENT_DELIVERY_REPLAYED,
                    delivery_id=delivery_id,
                    dispatch_id=dispatch_id,
                    task_id=task_id,
                    replays=row["replays"],
                    detail=reason,
                )
                row[DELIVERY_STATE_FIELD] = DELIVERY_STATE_PROCESSED
                row[DELIVERY_RECOVERED_FIELD] = False
                self._ack(delivery_id)
                continue
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
                    try:
                        payload = json.loads(message["payload"])
                    except (TypeError, ValueError) as error:
                        payload = {"_unparsable": " ".join(safe_text(error).split())}
                    provenance, mismatch = quiescence.worker_done_provenance(
                        payload,
                        expected_task_id=task_id,
                        expected_dispatch_id=dispatch_id,
                    )
                    if provenance == quiescence.PROVENANCE_MISMATCH:
                        # Recorded and discarded. It is NOT this waiter's result, and
                        # the reason it was refused is now an artifact rather than an
                        # acknowledgement with nothing behind it.
                        self._audit_coordinator(
                            run_logging.EVENT_DELIVERY_MISMATCH,
                            delivery_id=delivery_id,
                            dispatch_id=dispatch_id,
                            task_id=task_id,
                            message_id=message.get("id", ""),
                            reason_code=quiescence.PROVENANCE_MISMATCH,
                            detail=mismatch,
                        )
                        continue
                    if done is not None:
                        raise OrcaRuntimeError("worker_done was delivered more than once")
                    if payload.get("_orcaLifecycleRejection"):
                        raise OrcaRuntimeError("worker_done was rejected by Orca")
                    done = message
            if done is not None:
                # Marked consumed BEFORE the return: settle_attempt's STEP 3 owes this
                # delivery an ack, and until it lands nothing may arm another waiter
                # and the turn may not end.
                self._record_delivery_processed(
                    delivery_id,
                    task_id=task_id,
                    dispatch_id=dispatch_id,
                    message_id=done.get("id", ""),
                )
                return done, delivery_id
            self._ack(delivery_id)

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
            # OS-44. Zero LIFECYCLE mutations, which is what STEP 0's exactly-once
            # property is about -- but the delivery that carried us here was consumed
            # and still owes an acknowledgement. Discharging it here is the difference
            # between "this dispatch was already settled, nothing to redo" and a
            # Coordinator wedged behind an obligation nothing will ever clear: without
            # it, the ordering gate and the turn-end self-check would both keep
            # refusing forever. An already-acknowledged delivery issues no command at
            # all, so the replay path stays command-free in the ordinary case.
            #
            # OS-44 (BUGFIX-I2-G1-1). Discharging is NOT unconditional. It is safe only
            # where this delivery's own durable state proves there is nothing left to
            # recover, which delivery_disposition() answers from the two settlement
            # flags -- and those flags now flip only after their audit record is
            # published, so the answer is durable-backed whether the row was built by
            # this process or recovered from a predecessor's audit. A row that CLAIMED
            # a settlement and carries no settled record is the fail-closed case: a
            # lifecycle command may already have gone out and the record that would
            # prove the settlement finished does not exist, so acknowledging here would
            # hand the runtime's last copy of the delivery back over an audit that
            # cannot account for it -- and a successor would then find an unfinished
            # claim with no delivery left to recover it on. The claim-free rows -- a
            # fresh delivery carrying a duplicate or out-of-order worker_done for a
            # dispatch finalized elsewhere -- never entered the settlement path at all,
            # and the finalized ledger row above is their proof; those still discharge.
            if delivery_id in self.unacknowledged_deliveries():
                disposition, reason = quiescence.delivery_disposition(
                    delivery_id, self._deliveries
                )
                if disposition == quiescence.DELIVERY_RECOVER:
                    self._audit_coordinator(
                        run_logging.EVENT_DELIVERY_RECOVERY,
                        delivery_id=delivery_id,
                        dispatch_id=dispatch_id,
                        task_id=task_id,
                        reason_code=quiescence.DELIVERY_RECOVER,
                        detail=reason,
                    )
                    raise OrcaRuntimeError(reason)
                self._ack(delivery_id)
            return recorded

        # ==== STEP 0b. DURABLE SETTLEMENT CLAIM =============================
        # OS-44 (BUGFIX-I1-G1-1). Published BEFORE the first self.call(...) below, for
        # the same reason claim_settlement() itself must run before it: from this point
        # on a lifecycle command may have gone out, and a successor process that finds
        # this record without a matching settled record must recover explicitly instead
        # of repeating one. The write is fail-closed, so the claim cannot be missing
        # while the commands it authorises go out.
        self._record_delivery_settlement(
            delivery_id,
            DELIVERY_SETTLEMENT_CLAIMED_FIELD,
            run_logging.EVENT_DELIVERY_SETTLEMENT_CLAIMED,
            task_id=task_id,
            dispatch_id=dispatch_id,
        )

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
            self._ledger[dispatch_id]["unsettled_reason"] = safe_text(error)
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

        # ==== STEP 3. state + settlement reflection, COMPLETED before the ack ===
        # OS-44 (BUGFIX-I1-G1-1). The acknowledgement used to sit HERE, above the axis
        # accounting and the finalization below it. That ordering inverted the rule the
        # ticket states: a crash or exception in account_axes(), the RuntimeAttempt
        # construction or finalize_once() then landed AFTER a successful ack, so the
        # runtime considered the delivery consumed, nothing would ever redeliver it,
        # and the settlement ledger was left incomplete with no way back -- the
        # delivery was simply lost. Reflection is therefore completed first and the ack
        # is STEP 4; a failure anywhere in this block now leaves the delivery
        # unacknowledged, which both blocks the next waiter and keeps the runtime's
        # redelivery available as the recovery path.
        #
        # Safe to index: STEP 1b proved this payload carries an explicit outcome from
        # SETTLED_OUTCOMES, above the mutation, so this read can no longer be the
        # first place a malformed worker_done is noticed.
        payload = json.loads(done["payload"])

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
        # State and settlement are now fully reflected. Recorded durably here, in the
        # one window where "settled but not yet acknowledged" is true, so a crash
        # before the ack below is recovered as "acknowledge it, do nothing else"
        # instead of as a second settlement.
        self._record_delivery_settlement(
            delivery_id,
            DELIVERY_SETTLED_FIELD,
            run_logging.EVENT_DELIVERY_SETTLED,
            task_id=task_id,
            dispatch_id=dispatch_id,
        )

        # ==== STEP 4. delivery ack ==========================================
        # Only now, and before any next waiter: _check() refuses to arm one while this
        # delivery is outstanding, so the contract's order -- reflect, acknowledge,
        # then arm the next waiter -- is enforced by two gates rather than by comment.
        self._ack(delivery_id)
        return attempt

    # ---- OS-17: run-scoped ORCHESTRATOR_LOG.md / TIMING_LOG.md -----------------
    # SKILL.md section 9 has always named these two files as something a run
    # leaves behind under its own <ARTIFACT_ROOT>, but nothing in the actual
    # execution path wrote them. These three helpers are the whole fix: every
    # call site below hands them a RuntimeAttempt (or a status string) this
    # harness already built for its own return value, so no new state is
    # invented for logging's sake. Every write goes through _safe_log so a
    # logging failure -- a full disk, an unwritable path -- is recorded in
    # self._logging_errors and not raised into the caller, which would
    # otherwise turn an already-settled Dispatch into an apparent failure.
    #
    # OS-49 BUGFIX (review 5970292670, N-4). Stated with its actual width rather than
    # unconditionally: _safe_log catches `Exception`, so an ordinary logging failure is
    # recorded and absorbed, while KeyboardInterrupt / SystemExit / GeneratorExit
    # PROPAGATE through it as themselves. That split is correct and deliberate -- review
    # F-002 established that an operator's Ctrl-C arriving inside a slow writer is not a
    # logging failure to be papered over -- and the superseded "never raised into the
    # caller" phrasing described only its first half. Do not widen this to
    # `BaseException` to make the old sentence true.

    def _safe_log(self, writer: Any, *args: Any, **kwargs: Any) -> None:
        """Run one non-authoritative logging operation inside the guard.

        `writer` is any callable whose failure may not change a lifecycle decision --
        a `run_logging` writer, or (OS-49 BUGFIX, review 5970292670 N-1) a method of
        this class that PREPARES and then writes a row. Wrapping the preparation too is
        the point of accepting a method here: a row whose construction raises outside
        the guard is exactly as capable of unwinding a settled Dispatch as a failed
        write, and the guard that only covered the write did not say so.

        WIDTH, stated rather than implied (OS-49 BUGFIX, review 5970292670 N-4). This
        catches `Exception`. An ordinary logging failure is recorded and absorbed;
        KeyboardInterrupt, SystemExit and GeneratorExit PROPAGATE through it as
        themselves. The superseded section note said only "never raised into the
        caller", which described the first half. The split is deliberate -- review F-002
        established that an operator's Ctrl-C arriving inside a slow writer is not a
        logging failure to be papered over -- so do not widen this to `BaseException` in
        order to make the old sentence true.
        """
        try:
            writer(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 -- see the section note above
            # OS-49 BUGFIX iteration 2 (review F-001). Both halves of the row are
            # rendered through the total-for-`Exception` helpers. This guard's promise is
            # that an ordinary logging failure never reaches the caller, and an eager
            # `f"...{error}"` could break that promise from inside the guard itself -- the
            # same escape F-001 found on the model-selection boundary, with the
            # consequence section 9 forbids by name: an already-settled Dispatch turning
            # into an apparent failure.
            self._logging_errors.append(
                f"{_writer_label(writer)}: {safe_text(error)}"
            )

    def _audit_coordinator(self, event: str, **fields: Any) -> None:
        """One immutable record in this run's append-only Coordinator audit.

        OS-44 (BUGFIX-I1-G1-2). Deliberately NOT routed through _safe_log, unlike every
        other writer in this class. Section 9's "a logging failure never changes a
        lifecycle decision" holds for logs that are only ever read by humans; this
        family is different in kind, because it is the ONLY thing a successor process
        can recover the delivery ledger from. Swallowing a publication failure here
        would let processing and acknowledgement continue over an audit that no longer
        describes them, and the next process would then read an already-settled
        delivery as brand new -- the very defect this ticket removes, reintroduced by
        the logging guard. The failure is recorded in self._logging_errors AND raised,
        so the run stops at the boundary it could not record.

        A record written before start_run() has no run to belong to and is dropped;
        every OS-44 call site is inside a run.
        """
        if not self.run_id:
            return
        try:
            run_logging.append_coordinator_audit_record(
                self.run_id, event, dict(fields), base=self.artifact_dir
            )
        except Exception as error:  # noqa: BLE001 -- recorded and re-raised, never lost
            failure = safe_text(error)               # review F-001: total, see safe_text
            self._logging_errors.append(f"append_coordinator_audit_record: {failure}")
            raise OrcaRuntimeError(
                f"coordinator audit record {event!r} could not be published for run "
                f"{self.run_id} ({failure}); it is the only source a restarted "
                "Coordinator can recover the delivery ledger from, so the run fails "
                "closed here instead of continuing unrecoverably"
            ) from error

    def observe_orca_dispatch_state(self) -> dict[str, Any]:
        """Orca's own answer to "what is running, and what is runnable?".

        OS-44 (BUGFIX-I3-CRITICAL-1). The PR #31 review is right that the settlement
        ledger cannot answer this: a Dispatch enters that ledger through
        `claim_settlement()`, which runs only AFTER `wait_for_done()` has returned, so
        a ledger row can only ever describe a Worker or Reviewer that has already
        finished -- never one that is currently running. The authority is Orca's own
        Task and Dispatch records, and the rule that reads them is the one the shipped
        `run_workflow.py turn-end` boundary uses, not a second copy of it.
        """
        if not self.run_id:
            return {"active_dispatches": [], "runnable_actions": [], "task_count": 0,
                    "worker_count": 0}
        tasks = self.call("orchestration", "task-list", "--run", self.run_id)[
            "result"
        ].get("tasks")
        workers = self.call(
            "orchestration", "worker-list", "--run", self.run_id, allow_error=True
        )
        return turn_boundary.classify_orca_state(
            tasks, (workers.get("result") or {}).get("workers") if workers.get("ok") else []
        )

    def active_dispatch_count(self) -> int:
        """Dispatches Orca reports as currently running for this run.

        OS-44 (BUGFIX-I3-CRITICAL-1). Derived from `observe_orca_dispatch_state()` --
        Orca's Task status crossed with the Dispatch's own worker row -- and no longer
        from the settlement ledger, for the reason given there. The ledger count remains
        available under its own name (`unfinalized_ledger_dispatches()`) for the
        questions it can actually answer, but it is not this one.
        """
        return len(self.observe_orca_dispatch_state()["active_dispatches"])

    def unfinalized_ledger_dispatches(self) -> int:
        """Dispatches this Coordinator has claimed and not finalized.

        The finalize-once ledger's own count. It answers "what has this process claimed
        and not closed out?", which is a real question -- it is simply not the question
        "is a Worker running right now?", and OS-44 iteration 3 stopped using it as if
        it were.
        """
        return sum(
            1
            for row in self._ledger.values()
            if row.get("state") not in {"finalized", None}
        )

    def verify_quiescence(
        self,
        run_status: str,
        *,
        next_node: str = "",
        raise_on_violation: bool = True,
    ) -> dict[str, Any]:
        """OS-44. The self-check that runs immediately before the turn ends.

        A Coordinator turn may only end at one of `quiescence.QUIESCENT_STATES`: an
        active dispatch wait, WAITING_FOR_INPUT, BLOCKED, ESCALATED or COMPLETED (plus
        the CANCELLED/ABANDONED terminal statuses and their SETTLED lifecycle
        spelling). Ending anywhere else is the `run_c2166e75bb02` stall: a run that is
        neither terminal nor waiting, with zero active agents and a runnable next node,
        which nothing can wake.

        The judgement itself is the runtime-neutral contract's; this method supplies
        the two facts only the Coordinator holds -- how many dispatches are still
        unfinalized, and which deliveries it has processed without acknowledging -- and
        records the outcome either way. The verdict is returned as well as recorded so
        a caller that legitimately wants to report rather than raise (an already-failing
        error path, which must not have its original exception replaced) can pass
        `raise_on_violation=False`.
        """
        verdict = quiescence.quiescence_verdict(
            run_status=run_status,
            next_node=next_node,
            active_dispatches=self.active_dispatch_count(),
            unacknowledged_deliveries=self.unacknowledged_deliveries(),
        )
        self._audit_coordinator(
            run_logging.EVENT_QUIESCENCE_VERIFIED
            if verdict["quiescent"]
            else run_logging.EVENT_QUIESCENCE_VIOLATION,
            run_status=run_status,
            next_node=next_node,
            active_dispatches=verdict["active_dispatches"],
            reason_code=verdict["reason_code"],
            detail=verdict["detail"],
            delivery_id=(verdict["unacknowledged_deliveries"] or [""])[0],
        )
        if raise_on_violation and not verdict["quiescent"]:
            raise OrcaRuntimeError(
                f"coordinator turn may not end ({verdict['reason_code']}): "
                f"{verdict['detail']}"
            )
        return verdict

    def restore_delivery_ledger(self) -> dict[str, dict[str, Any]]:
        """Rebuild the delivery ledger a previous PROCESS left in the run's audit.

        The process-restart half of the contract. A fresh Coordinator over an existing
        run starts with an empty in-memory ledger, so a redelivered and
        already-acknowledged delivery would read as a first processing and be adopted
        as the new waiter's result -- the same defect, one process later. Folding the
        append-only audit forward restores which deliveries were processed, which were
        acknowledged and how many times each was replayed.

        Rows this process already holds win: a live row is newer than the artifact it
        was derived from.

        OS-44 (BUGFIX-I1-G1-2). An audit that cannot be READ is fatal, not a recorded
        warning. This is the only source that can tell an already-processed delivery
        from a new one; continuing without it means the next waiter may adopt a replay,
        which is the defect itself. Callers reach this through
        _restore_delivery_ledger_once() on the production wait path, so the recovery
        cannot be skipped by forgetting to ask for it.

        OS-44 (FINAL-R1). The replay itself now refuses a published audit record it
        cannot fold verbatim, and it signals that with ``CoordinatorAuditError``. That
        type is named in the except clause rather than left to its ``ValueError`` base,
        because "a corrupt record reaches this handler and is converted into a refusal
        BEFORE any waiter is armed" is the property being relied on, not an incidental
        consequence of an exception hierarchy.
        """
        if not self.run_id:
            return dict(self._deliveries)
        try:
            restored = run_logging.replay_delivery_ledger(
                self.run_id, base=self.artifact_dir
            )
        except (OSError, run_logging.CoordinatorAuditError, ValueError) as error:
            failure = safe_text(error)               # review F-001: total, see safe_text
            self._logging_errors.append(f"replay_delivery_ledger: {failure}")
            raise OrcaRuntimeError(
                f"the coordinator audit for run {self.run_id} could not be read "
                f"({failure}); it is the only source that distinguishes an "
                "already-processed delivery from a new one, so the Coordinator fails "
                "closed rather than arm a waiter that could adopt a replay"
            ) from error
        for delivery_id, row in restored.items():
            if delivery_id in self._deliveries:
                continue
            self._deliveries[delivery_id] = {
                "delivery_id": delivery_id,
                "task_id": row.get("task_id", ""),
                "dispatch_id": row.get("dispatch_id", ""),
                "message_id": "",
                # OS-44 (BUGFIX-I3-MAJOR-1). The folded state is carried VERBATIM. It
                # used to default to `acknowledged` when the audit named no state, which
                # is the one direction a recovery must never guess in: it turned "the
                # audit does not say this was acknowledged" into "it was", and that is
                # exactly how the post-wire-ack crash window stayed invisible. A blank
                # state now stays blank and `quiescence.delivery_obligation` decides
                # what is owed from how far the predecessor actually got.
                DELIVERY_STATE_FIELD: row.get(DELIVERY_STATE_FIELD) or "",
                "replays": int(row.get("replays") or 0),
                "ack_attempts": 0,
                "ack_error": "",
                DELIVERY_SETTLEMENT_CLAIMED_FIELD: bool(
                    row.get(DELIVERY_SETTLEMENT_CLAIMED_FIELD)
                ),
                DELIVERY_SETTLED_FIELD: bool(row.get(DELIVERY_SETTLED_FIELD)),
                DELIVERY_ACK_INTENT_FIELD: bool(row.get(DELIVERY_ACK_INTENT_FIELD)),
                DELIVERY_RECOVERED_FIELD: True,
            }
        self._deliveries_restored_for = self.run_id
        return dict(self._deliveries)

    def _restore_delivery_ledger_once(self) -> None:
        """Recover the predecessor process's delivery ledger, once, before any waiter.

        OS-44 (BUGFIX-I1-G1-2). Called by _check(), which is the single point every
        `check --wait` in this class goes through, so restart recovery belongs to the
        production wait path rather than to a helper a caller has to remember. A run
        this process created marks itself recovered in start_run(); a run it merely
        BOUND (resume_run) is recovered here, or by resume_run itself, whichever comes
        first -- and either way strictly before a waiter can be armed.
        """
        if not self.run_id or self._deliveries_restored_for == self.run_id:
            return
        self.restore_delivery_ledger()
        # OS-44 (BUGFIX-I3-MAJOR-1). Recovery is not finished when the rows are back:
        # an acknowledgement a predecessor left open has to be CLOSED, not merely
        # observed. Closing it here -- inside the once-only restore, which both _check()
        # and resume_run() go through -- is what makes the post-wire-ack crash window
        # recoverable without depending on the runtime redelivering anything, and what
        # stops a successor proceeding over an obligation it never recorded an outcome
        # for.
        self.reconcile_recovered_acknowledgements()

    def resume_run(
        self,
        run_id: str,
        *,
        run_owner: str,
        requested_phases: tuple[str, ...] = (),
    ) -> str:
        """Bind a successor Coordinator process to an EXISTING Run, and recover.

        OS-44 (BUGFIX-I1-G1-2). start_run() creates a new Run and is therefore not the
        path a restarted Coordinator takes; this is. A successor's in-memory delivery
        ledger is empty, so without recovery a redelivered, already-handled delivery
        reads as a first processing and is adopted as the new waiter's result -- the
        recorded defect, one process later. The recovery happens HERE, before this
        method returns and therefore before any caller can reach `check --wait`, and it
        fails closed when the audit it depends on cannot be read.
        """
        if not run_id:
            raise OrcaRuntimeError("resume_run requires the id of an existing Run")
        for candidate in requested_phases:
            require_workflow_phase(candidate, field="requested_phases")
        self.run_id = run_id
        self.run_owner = run_owner
        # OS-44 (BUGFIX-I4-R1-REAL-PATH). A successor process is a NEW Claude Code
        # session driving an OLD Run, so it publishes its own binding here for the same
        # reason start_run() does: the predecessor's binding names the predecessor's
        # session and cannot gate this one's turn ends.
        self._bind_turn_boundary_session()
        self.requested_phases = tuple(requested_phases)
        # A fresh process holds no rows for this run. Stated rather than assumed, so
        # binding a second run on one instance cannot inherit the first run's ledger.
        self._deliveries = {}
        self._deliveries_restored_for = ""
        if run_owner not in self._terminals:
            self.register_terminal(
                run_owner, role="run_owner_fixture", origin="adopted"
            )
        self._restore_delivery_ledger_once()
        return run_id

    def _bind_turn_boundary_session(self) -> str:
        """Publish the durable session -> Run binding the turn-end Stop hook reads.

        OS-44 (BUGFIX-I4-R1-REAL-PATH). The registered hook is fired by the runtime for
        a session, and the runtime has no idea which Orca Run that session drives; this
        record is how it finds out. Written where the run's other durable state lives,
        keyed by the session id Claude Code exports into this process and sends in the
        hook payload, so the two are the same value by construction.

        Returns the path written, or "" when there was nothing to bind or the record
        could not be published. Does not raise on an ordinary `Exception`: this is a
        convenience for a hook that is opt-in, and it may not be able to fail a Run.
        Control-flow exceptions (KeyboardInterrupt / SystemExit / GeneratorExit) are not
        caught and propagate -- the N-4 split, stated here too because this is the same
        unconditional phrasing in the same module.
        """
        try:
            path = turn_boundary.bind_session_run(
                self.run_id or "", artifact_base=self.artifact_dir
            )
        except Exception:  # noqa: BLE001 - a binding may never fail a Run
            return ""
        # OS-43. The one additive call: start publishing this Coordinator's LIVENESS
        # lease for the Run it just bound. The binding above is a record of INTENT and
        # says nothing about a beating heart; the liveness lease is refreshed on a
        # cadence INDEPENDENT of any claimed section, which is what makes "the
        # Coordinator's heartbeat expired" a fact rather than a false positive.
        # Under the same discipline as the binding, carrying the same N-4 qualification
        # the docstring above now states: on an ordinary `Exception` a liveness record
        # may no more fail a Run than a binding may, while KeyboardInterrupt /
        # SystemExit / GeneratorExit are not caught and propagate. The unconditional
        # phrasing that stood here (OS-49 BUGFIX iteration 2, review F-001) contradicted
        # the `except Exception` three lines above it. Wording only; no capture moves.
        self._begin_turn_boundary_liveness()
        return str(path) if path is not None else ""

    #: Opt-out for the liveness producer, for an operator who runs the Coordinator loop
    #: somewhere else and publishes the lease with `run_workflow.py turn-end-liveness`.
    #: Any value other than "0"/"false"/"no" leaves it on.
    LIVENESS_ENV = "ORCA_OS43_COORDINATOR_LIVENESS"

    def _begin_turn_boundary_liveness(self) -> None:
        """Start the OS-43 liveness keeper for `self.run_id`, retiring any predecessor.

        Does not raise on an ordinary `Exception`, for the same reason
        `_bind_turn_boundary_session` does not: this is an observability producer, and it
        may not fail a Run. Control-flow exceptions propagate (review 5970292670, N-4).

        Binding a second Run on one instance retires the first Run's keeper first, so an
        instance never keeps a lease alive for a Run it has moved on from -- the mirror
        of `stop()`'s own rule in `lease_keeper`.
        """
        if os.environ.get(self.LIVENESS_ENV, "1").strip().lower() in (
            "0",
            "false",
            "no",
        ):
            return
        self._end_turn_boundary_liveness()
        run_id = self.run_id or ""
        if not run_id:
            return
        try:
            self._liveness_keeper = turn_boundary.begin_run_liveness(
                run_id, artifact_base=self.artifact_dir
            )
            self._liveness_run_id = run_id
        except Exception:  # noqa: BLE001 - liveness may never fail a Run
            self._liveness_keeper = None
            self._liveness_run_id = ""

    def _end_turn_boundary_liveness(self) -> None:
        """Retire the liveness keeper and record the release.

        Does not raise on an ordinary `Exception`; control-flow exceptions propagate --
        the same N-4 split as every other guard in this module.
        """
        keeper = getattr(self, "_liveness_keeper", None)
        run_id = getattr(self, "_liveness_run_id", "")
        self._liveness_keeper = None
        self._liveness_run_id = ""
        if keeper is None or not run_id:
            return
        try:
            turn_boundary.end_run_liveness(
                keeper, run_id, artifact_base=self.artifact_dir
            )
        except Exception:  # noqa: BLE001 - cleanup may never fail a Run
            return

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
        # OS-49 V8. ONE sibling row per settled dispatch, emitted immediately BEFORE the
        # settled row, from this same funnel and under the same _safe_log guard -- so
        # every round kind and both initiators are covered by one piece of code.
        #
        # A new EVENT NAME, never a new COLUMN: every reader does
        # `if len(cells) != len(ORCHESTRATOR_LOG_COLUMNS): continue`, so a new column
        # would leave historical rows on disk and make all of them invisible. And never an
        # overload of the settled row's `detail`, which is the OS-17 body_excerpt
        # diagnostic -- appending to it would redefine an existing event's existing
        # column.
        #
        # OS-49 BUGFIX (review 5970292670, N-1b). The WHOLE operation goes through
        # `_safe_log`, not just the write inside it. This call used to be direct, and
        # "under the same _safe_log guard" was true only of the final writer call: the
        # routing read, the `_routing_is_model_aware()` predicate, `_routing_key()`, the
        # two `_model_identity` lookups and the `detail` assembly -- which RENDERS driver
        # -supplied evidence fields -- all ran outside it. By the time `_log_attempt()` is
        # reached the dispatch has SETTLED, so an exception from any of those unwound
        # `run_existing_task()` between `settle_attempt()` and its return: it interrupted
        # normal return of an already-settled result and skipped the three recording steps
        # below (the settled row, the timing row and, on a Final Review, the audit record).
        # That is the non-authoritative-logging contract broken from inside the funnel
        # that states it.
        #
        # Deliberately NARROW, and this is the line the review draws explicitly. It wraps
        # ONE non-authoritative row. `_log_attempt()` itself is NOT blanket-wrapped, and
        # the two authoritative families keep their fail-closed behaviour exactly:
        # `_record_decision_from_attempt()` above still raises, and `_audit_coordinator()`
        # still records AND re-raises, because a Coordinator audit record is the only
        # thing a restarted Coordinator can recover the delivery ledger from. `_safe_log`
        # catches `Exception`, so KeyboardInterrupt / SystemExit / GeneratorExit still
        # propagate and the F-002 interrupt defect is not reintroduced.
        self._safe_log(
            self._log_agent_identity_row,
            phase=phase, attempt=attempt, terminal_created=terminal_created,
            round_kind=round_kind,
        )
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

    def _log_agent_identity_row(
        self,
        *,
        phase: str | None,
        attempt: "RuntimeAttempt",
        terminal_created: bool,
        round_kind: str,
    ) -> None:
        """The durable answer to "which effective agent identity produced this result?".

        Emitted ONLY when this run's routing actually carries model identity, mirroring
        `RunRouting.evidence_rows()`'s own rule that a legacy routing produces nothing at
        all -- so a legacy run's logs stay byte-identical. Absence is unambiguous because
        the run-scoped `agent_profile_selected` / `agent_routing_resolved` rows already
        say up front whether this run's routing carries a model.

        Provenance is NOT the gate. This runs after settlement, and its CALL SITE passes
        the whole method through `_safe_log`, so an ordinary failure anywhere in it --
        predicate, routing lookup, row construction or the write -- lands in
        `self._logging_errors` and does not unwind an already-settled Dispatch. That is
        only safe because enforcement lives in the two gates: a missing identity row can
        never be what PERMITTED an unverified delivery.

        OS-49 BUGFIX (review 5970292670, N-1b). The superseded sentence said "This runs
        after settlement inside `_safe_log`", and for the PREPARATION phase it was simply
        false: the caller invoked this method directly and only the writer call at the
        bottom was guarded, so everything above that line -- including `detail`, which
        renders driver-supplied evidence fields -- ran outside the guard. The fix is at
        the call site in `_log_attempt()`, which is why this docstring now says where the
        boundary actually is instead of asserting a property this body cannot provide for
        itself. The inner `_safe_log` around the writer is KEPT: it is not redundant, it
        is what makes a failed WRITE record itself under the writer's own name
        (`log_orchestrator_event: ...`) rather than under this method's.
        """
        routing = self.agent_routing
        if not self._routing_is_model_aware(routing):
            return
        phase_name = phase or ""
        role = attempt.role or ""
        # OS-49 iteration 2 (review F-001): BOTH halves of the key come from the one
        # mapping. Re-spelling the entry phase here was a second, narrower copy of the
        # Final Reviewer rule that recognised only the `final_reviewer` role string, so
        # this row read a different slot than the barrier wrote.
        entry_phase, routing_role = self._routing_key(role, phase_name)
        entry = self._routing_entry_for(role, phase_name)
        evidence = self._model_identity.get((entry_phase, routing_role))
        if evidence is None:
            evidence = self._model_identity.get((phase_name, routing_role))
        state = evidence.state if evidence is not None else MODEL_EVIDENCE_NONE
        detail = " ".join(
            (
                f"command={entry.command if entry is not None else ''}",
                f"requested_model={(entry.model if entry is not None else '') or 'none'}",
                f"resolved_model={(evidence.resolved_model if evidence else '') or 'none'}",
                f"request_method={(evidence.request_method if evidence else '') or 'none'}",
                f"selection_token={(evidence.selection_token if evidence else '') or 'none'}",
                f"request_stamp={evidence.request_stamp if evidence else 0}",
                f"observe_stamp={evidence.observe_stamp if evidence else 0}",
                f"observation_method={(evidence.observation_method if evidence else '') or 'none'}",
                f"selection_capability={(evidence.capability if evidence else '') or 'none'}",
                "selection_verified="
                + ("true" if state == MODEL_EVIDENCE_VERIFIED else "false"),
                f"profile={routing.profile_name or 'none'}",
                f"profile_source={routing.profile_source or 'none'}",
                f"schema={getattr(routing, 'schema_version', 0)}",
            )
        )
        self._safe_log(
            run_logging.log_orchestrator_event,
            self.run_id,
            base=self.artifact_dir,
            event=EVENT_AGENT_IDENTITY_BOUND,
            phase=phase_name,
            role=role,
            iteration=attempt.iteration,
            task_id=attempt.task_id,
            dispatch_id=attempt.dispatch_id,
            terminal=attempt.terminal,
            action="created" if terminal_created else "reused",
            reuse=attempt.terminal_effect,
            risk=self.risk,
            round_kind=round_kind,
            # Exactly ONE of the six model-evidence states, so "did this dispatch run on
            # a positively verified model?" is a COLUMN SCAN rather than a detail parse,
            # and `verified` can never be inferred from the absence of anything.
            result=f"model_state={state}",
            detail=detail,
        )

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
        # The evidence the barrier ACCEPTED for this Final Review attempt, or None when no
        # model was declared. Read from the run-scoped map rather than re-observed: a Final
        # Review that re-derived an identity could silently differ from the one the run was
        # routed with, which is exactly the drift OS-49 forbids.
        final_evidence = self._model_identity.get(
            (FINAL_REVIEW_PHASE, "final_reviewer")
        )
        # OS-49 iteration 2 (review F-001). The five model fields are ONE bundle written
        # from ONE source. They used to come from two: `reviewer_requested_model` from the
        # routing and the other four from the evidence, so a record could DECLARE a
        # requested model while carrying no evidence that anything resolved it -- an
        # internally contradictory record, which is exactly the shape reuse condition 9
        # refuses to act on.
        #
        # The routing-key fix above already makes that shape unreachable rather than
        # unlikely: the barrier precedes BOTH delivery acts, settlement follows delivery,
        # and this method only runs on a settled attempt -- so a Final Review record can
        # only exist after the declared model was positively verified. This is the second
        # lock, local to the record: the declared value is emitted only together with the
        # evidence the barrier's leg (g) proved echoed it back. It is still read from the
        # MATERIALIZED routing and still never re-derived here; it simply cannot appear
        # alone. With no model declared, or on any attempt whose evidence is absent, all
        # five fields stay empty/`none` -- and the identity row for the same dispatch
        # still records the declared token beside `model_state=none`, so nothing the run
        # observed is lost from its durable evidence.
        declared_model = entry.model if entry is not None and entry.resolved else ""
        model_verified = (
            final_evidence is not None
            and final_evidence.state == MODEL_EVIDENCE_VERIFIED
            and final_evidence.requested_model == declared_model
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
            # OS-49. The Final Reviewer's model identity, read from the MATERIALIZED
            # routing and the evidence the barrier accepted -- never re-derived here. That
            # is what makes the Final Adversarial Review preserve the identity the run was
            # routed with rather than silently observe a different one.
            reviewer_requested_model=declared_model if model_verified else "",
            reviewer_resolved_model=(
                final_evidence.resolved_model if model_verified else ""
            ),
            reviewer_model_state=(
                final_evidence.state if model_verified else MODEL_EVIDENCE_NONE
            ),
            reviewer_model_request_method=(
                final_evidence.request_method if model_verified else ""
            ),
            reviewer_model_request_evidence=(
                final_evidence.request_evidence if model_verified else ""
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
        repair_instruction: Any | None = None,
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
        # ---- OS-42 F-002. The bounded validation repair, and only it. -----------------
        # A settled boundary whose gate declaration was defective publishes NO record and
        # advances `_last_settled` anyway, so the next ordinary B1 refuses as UNBOUND.
        # That is the correct fail-closed answer for every dispatch that would MOVE PAST
        # the boundary -- and it is the wrong one for the single dispatch that re-asks
        # it. A repair carries the same run, phase and gate iteration, writes to the same
        # artifact, consumes no Worker/Reviewer iteration, and is bounded by
        # MAX_REPAIR_ATTEMPTS in the engine that issues it. Admitting it here does not
        # weaken OS-29's guarantee by one clause: no record has been published, none is
        # being admitted, and the very next non-repair dispatch meets the identical
        # refusal unless the repair actually published one.
        #
        # The three conjuncts are all required. `repair_instruction` is non-None on
        # exactly the repair dispatches (`contracts.make_intent` makes the biconditional
        # structural); `_last_input_defect` is set only where a defect was recorded and
        # cleared the moment a record is published; and the round must be the SAME one.
        if (repair_instruction is not None
                and self._last_input_defect is not None
                and self._last_input_defect == (self.run_id, phase or "", iteration)):
            self._pending_verification = None
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
        # A dispatch decides ONCE. See `_settled_dispatch_decisions` for why this funnel
        # is reached twice for one dispatch at all.
        if attempt.dispatch_id:
            decided = self._settled_dispatch_decisions.get(attempt.dispatch_id)
            if decided is not None:
                return decided
        decision = self._judge_settlement(phase=phase, attempt=attempt)
        if attempt.dispatch_id:
            self._settled_dispatch_decisions[attempt.dispatch_id] = decision
        return decision

    def _judge_settlement(
        self, *, phase: str | None, attempt: "RuntimeAttempt"
    ) -> tuple[str, str]:
        """The judgement itself, for a dispatch that has not been judged before."""
        body = attempt.body or ""
        settled = (self.run_id, phase or "", attempt.iteration)
        if not decision_gate.declares_gate_result(body):
            # Named separately from the parse failure below only so the reason code
            # says WHICH fail-closed clause fired; the effect is identical.
            self._last_settled = settled
            self._last_input_defect = settled
            return decision_gate.INPUT_DEFECT_STATE, decision_gate.GATE_INPUT_MISSING
        try:
            gate = decision_gate.parse_gate_result(body, self._decision_policy)
        except decision_gate.GateRefusal as refusal:
            self._pending_verification = None
            self._last_settled = settled
            self._last_input_defect = settled
            return decision_gate.INPUT_DEFECT_STATE, refusal.reason
        # ---- OS-42 F-001 (round 2). THE MECHANICS IDENTITY OF THE RAW RECORD, BEFORE
        # any normalisation and before the ledger append below.
        #
        # `parse_gate_result` above validates the DECISION -- the closed field set and
        # the OS-28 policy -- and binds nothing to the dispatch that produced it. The
        # `record.update({...})` further down then overwrites `run`, `phase`,
        # `iteration`, `boundary`, `source` and `role` with this Coordinator's own
        # values. Together those two facts meant a settlement declaring the internally
        # valid but FOREIGN identity `run_foreign/design/99/B3/reviewer/reviewer`
        # returned CLEAR and was published as `<this run>/<this phase>/1/B2/worker/
        # worker`: the forgery was not refused, it was rewritten into a valid-looking
        # ledger row. No later classifier can withdraw an accepted ledger write, which
        # is why the check has to be HERE, at the first live consumer, and not only in
        # the engine's VALIDATE_SETTLEMENT.
        #
        # `mechanics_identity_defects` is the SAME judgement the engine's
        # `classify_gate` makes -- round-3 F-001 removed the "judge only what is
        # declared" exemption this check shipped with. A record that omits its mechanics
        # identity has not exercised a division of labour: the generated contract GIVES
        # every dispatched record those fields, so omission is a correctable format
        # error, and the bounded repair loop re-asks for a complete record rather than
        # the Coordinator inventing one and publishing it.
        mechanics = decision_contract.mechanics_identity_defects(
            gate.record,
            role="reviewer" if attempt.role.endswith("reviewer") else "worker",
            binding={"run": self.run_id, "phase": phase or "",
                     "iteration": attempt.iteration},
        )
        if mechanics:
            # An EQUALITY, exactly as `validate_settlement_node` uses: a list carrying
            # even one identity defect is not repairable.
            repairable = {defect.kind for defect in mechanics} == {"FORM"}
            code = (decision_gate.GATE_INPUT_MALFORMED if repairable
                    else decision_gate.GATE_INPUT_UNBOUND)
            self._pending_verification = None
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
                decision_reason_code=code,
                detail=" ".join(
                    "; ".join(defect.message for defect in mechanics).split())[:200],
            )
            self._last_settled = settled
            # Repairable defects arm the bounded re-ask; an identity claim explicitly
            # DISARMS it, and disarms one an earlier repairable attempt on this same
            # round had armed. Otherwise a malformed first attempt would buy a forged
            # second one a re-ask, and forgery would be repaired into acceptance one
            # round later.
            self._last_input_defect = settled if repairable else None
            return decision_gate.INPUT_DEFECT_STATE, code
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
            self._last_input_defect = settled
            return (
                decision_gate.INPUT_DEFECT_STATE,
                decision_gate.GATE_INPUT_UNBOUND,
            )
        record = dict(gate.record)
        # Every mechanics field below has already been proven PRESENT and equal to the
        # value written here (see the identity gate above), so this update is now a
        # canonicalisation of an already-validated record plus the ledger-owned fields
        # the agent never supplies. It can neither normalise a foreign claim into a local
        # one nor invent an identity the agent did not declare.
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
            self._logging_errors.append(
                f"append_decision_ledger_record: {safe_text(error)}"
            )
        self._last_settled = settled
        # A record was published for this round, so the round is bound and no repair
        # may claim it again.
        self._last_input_defect = None
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
        # OS-31: on the Skill-document path, WAITING_FOR_INPUT is now the status a
        # PAUSABLE decision block writes, so it must publish the clarification exactly as
        # BLOCKED does -- otherwise a paused run would wait for a question nobody asked.
        if status in ("BLOCKED", "WAITING_FOR_INPUT"):
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
            error = json.dumps({"exception":safe_type_name(exc),"message":safe_text(exc),"ledger_keys":keys},
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
        terminal_observer: Callable[[str], None] | None = None,
        terminal_title: str | None = None,
        terminal_worktree: str | None = None,
        repair_instruction: Any | None = None,
    ) -> tuple[RuntimeAttempt, str]:
        """Dispatch and settle a Task that already exists (the graph-first path).

        `terminal_observer`, `terminal_title` and `terminal_worktree` are the OS-31 seams
        (SS4.2.1). The observer is invoked with the handle immediately after the terminal is
        created and immediately BEFORE `start_worker`, which is the only point at which a
        durable write can sit between the two effects; it issues no Orca command and
        observes one string. All three default to None, so every existing call site binds
        unchanged.

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
            phase=phase, role=role, iteration=iteration, verifies=verifies,
            repair_instruction=repair_instruction,
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
                # OS-42: present only on a repair dispatch. It cannot be derived from
                # (role, phase, iteration, run_id) the way the contract block can --
                # it originates in a SETTLEMENT -- so it is threaded explicitly.
                repair_instruction=repair_instruction,
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
            title=terminal_title,
            worktree=terminal_worktree if terminal_worktree else "current",
        )
        if terminal_observer is not None:
            # Strictly between `terminal create` and `worker-start`: the digest becomes
            # durable before the terminal is adopted, so a crash after adoption still
            # leaves an identity a successor can verify against.
            terminal_observer(handle)
        dispatch_started_at = run_logging.now_iso()
        # round 5 review MAJOR: opened here, before start_worker(), so phase_start/
        # iteration_start actually brackets this dispatch instead of trailing it.
        self._open_phase_iteration_boundary(
            phase or "", iteration, opened_at=dispatch_started_at
        )
        # OS-49: the barrier's attempt identity. Threaded from the values this dispatch
        # already has -- never defaulted, never inferred from unrelated state -- because
        # omitting them on a model-aware run is a refusal, not a skip.
        dispatch_id, supervised = self.start_worker(
            task_id, handle, spec,
            role=role, phase=self._barrier_phase(role, phase), attempt=iteration,
        )
        done, delivery_id = self.wait_for_done(dispatch_id, task_id)
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
        dispatch_id, supervised = self.start_worker(
            task_id, handle, spec,
            role=role, phase=self._barrier_phase(role, phase), attempt=iteration,
        )
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
        # ---- OS-44. The quiescence self-check, immediately before the turn ends and
        # after every artifact this run owes has been written. `result.status` is one
        # of run_logging.RUN_STATUS_VALUES for every scenario this harness defines --
        # all of them terminal or WAITING_FOR_INPUT -- so this reports rather than
        # raises on the normal path; it raises exactly when the run is about to be left
        # idle, non-terminal and unwakeable, or with a delivery still unacknowledged.
        # It runs BEFORE the per-run state below is cleared, because clearing it first
        # would make every turn look quiescent.
        self.verify_quiescence(result.status)
        self.run_owner = None
        self.run_id = None
        self._timing = None
        self._raw = []
        self._signals = []
        self._terminals = {}
        self._ledger = {}
        self._deliveries = {}
        self._deliveries_restored_for = ""
        # OS-49 iteration 2 (review F-002). The model-identity maps are per-run state
        # exactly like the three collections above: every record in them was accepted
        # against ONE run's six-part attempt key, and `run_runtime_scenarios()` drives
        # several start_run/finish cycles on one harness instance. Leaving them behind
        # let the next run read a previous run's counterpart evidence as its own pair
        # admission, and let leg (i) compare a new run's model against a record no
        # session in that run produced.
        #
        # OS-49 BUGFIX (review B1). `start_run()` now clears the same state at the run
        # boundary, which is the reset that actually closes the leak -- a run that never
        # reaches this method cannot be cleaned up by it. These statements stay because
        # they are still correct and still useful: a cleanly finished run should not leave
        # model records live in the interval before the next `start_run()`, where a stray
        # read would find them. Two resets, neither of them load-bearing alone.
        self._model_identity = {}
        self._model_pending_evidence = {}
        self._model_session_identity = {}
        # OS-49 BUGFIX (review B2). The identity history is run-scoped exactly like the
        # three authoritative maps above: "append-only for the life of the RUN".
        self._model_role_history = {}
        self._model_session_history = {}
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

    def next_terminal(
        previous: RuntimeAttempt | None, role: str, phase: str = ""
    ) -> str | None:
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
            # OS-49 condition 9. The MODEL the NEXT dispatch requests, read from the
            # materialized routing for the role and phase that dispatch will run -- never
            # from the previous dispatch's own record, which is the value condition 9
            # compares AGAINST. "" on every run that declares no model, which is what
            # keeps this scenario's recorded answers byte-identical there.
            requested_model=harness.resolved_agent_model(role, phase),
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
            terminal=next_terminal(worker_previous, "worker", phase),
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
            terminal=next_terminal(reviewer_previous, "reviewer", phase),
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
