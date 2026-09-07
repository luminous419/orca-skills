"""OS-44 (BUGFIX-I3-CRITICAL-1).  The executable Coordinator turn-end boundary.

``quiescence.py`` owns the RULE and stays pure.  This module is the only place that
goes and *finds out* whether the rule is satisfied, and the CLI verb it backs
(``run_workflow.py turn-end``) is the invocable control point a live, prompt-driven
Coordinator runs before it returns a response.

WHAT THIS ENFORCES, EXACTLY
---------------------------
When ``turn-end`` is invoked it derives, in one command, from state nobody has to be
trusted about:

* **active dispatch** -- ``orca orchestration task-list --run`` and
  ``orca orchestration worker-list --run``.  A dispatch counts as active only when both
  authorities agree it is: the Task is in Orca's ``dispatched`` status AND a worker row
  for that Dispatch reports a worker that has not finished.  A ``dispatched`` Task with
  no such row proves nothing is running and is reported as RECOVERY WORK, which refuses
  the turn -- absence of evidence is never read as evidence of a wait.  This also
  replaces the settlement-ledger derivation the PR #31 review rejected: a Dispatch
  enters that ledger through ``claim_settlement()`` only *after* ``wait_for_done()`` has
  already returned, so the ledger can never represent a Worker or Reviewer that is
  currently running -- it represents one that has already finished.
* **run status and the graph's next node** -- the run's durable OS-40 checkpoint store,
  when it has one: the status from the committed ``terminal_status`` / ``run_lifecycle``
  and the next node from the engine's own ``routing.route``.  A run with no checkpoint
  store -- the ordinary case for the prompt-driven Coordinator, which never executes the
  LangGraph engine -- has no durable run-status authority, and the boundary says so
  (``status_authority`` is ``declared_only``) instead of dressing a declaration up as a
  derivation.  A declared status that CONTRADICTS an authoritative one is refused.
* **runnable work** -- Orca Tasks that are unblocked (every dependency ``completed``)
  and not dispatched, Tasks whose worker has finished but whose ``worker_done`` has not
  been collected, dispatched Tasks no live worker row corroborates, and the checkpoint's
  own next node.  Any of them with no active dispatch is the ``run_c2166e75bb02`` shape.
* **outstanding deliveries** -- folded out of the run's own append-only
  ``coordinator_audit/`` through ``run_logging.replay_delivery_ledger``, and classified
  by ``quiescence.delivery_obligation``.  An unreadable audit fails closed.  EVERY open
  obligation refuses the turn here, including the ones the in-process waiter gate
  deliberately stands aside for: that gate may distinguish a predecessor's row (which a
  redelivery closes, on the waiter counting it would refuse to arm) from this process's
  own, because it knows which rows it wrote.  A separate process reading the audit does
  not, and a turn is not at rest with any delivery obligation open in either case.
* **the durable wait** -- a declared ``WAITING_FOR_INPUT`` is corroborated against an
  OS-31 pause record, a published OS-30 clarification request, or an open Orca decision
  gate.  A wait that exists only in the response text is refused.

If the resulting state is not one the contract calls rest, the command exits non-zero
with the reason code and the detail, and publishes a ``quiescence_violation`` record to
the run's audit.  A turn that ends on a natural-language progress report -- work
runnable, nothing dispatched, nothing armed -- is refused, and so is a rest state the
run's own state does not support (``COMPLETED`` with a Task still dispatched,
``WAITING_FOR_INPUT`` with no wait armed).

HOW IT IS INVOKED, AND WHAT THAT DOES AND DOES NOT GUARANTEE
------------------------------------------------------------
Two entry points, and they are not equivalent.

``run_workflow.py turn-end`` is invoked by whoever remembers to invoke it.
``run_workflow.py turn-end-hook`` is the same derivation wired to Claude Code's ``Stop``
event, which the installed runtime fires when the model finishes responding and which
can BLOCK: a blocking Stop hook's reason is pushed onto the conversation and the model
is re-invoked instead of the turn ending.  This was verified against the installed
build, not assumed -- see the ``Stop-hook boundary`` section below for what was observed
in Claude Code 2.1.260 and in the live settings file.

ENFORCED, once the hook is registered in a project that holds Orca run state -- that
prerequisite is the whole of it, and it is stated this precisely because an earlier round
claimed enforcement "once the hook is registered" while allowing every turn of a session
that had not run a second, separately remembered command:

1. **Automatic invocation at the real turn end.**  The boundary runs because the runtime
   ran it, not because a model remembered to.
2. **A refusal actually blocks.**  The turn does not end; the reason goes to the model.
3. **Fail-closed on everything except positive evidence of irrelevance.**  Unreadable
   authoritative state, a session this boundary cannot attribute to a Run, a registration
   whose entry point does not resolve, and an unexpected failure of the boundary itself
   ALL block the turn, each bounded by the consecutive-block cap below.  The single
   silent allow is a project PROVEN to hold no ``artifacts/runs`` directories: the runs
   root was read, or its absence was established, and there was nothing there.  That --
   and only that -- is evidence the session is unrelated.  ``project_run_state()`` is
   what distinguishes it, and it answers three ways rather than two on purpose: a runs
   root that cannot be READ is ``unreadable``, never ``proven_absent``, and it blocks.
   In particular a Coordinator that never ran ``turn-end-bind`` is refused and told to
   bind, rather than being allowed and merely told; an allow does not re-invoke the
   model, so an announcement can never repair the thing it announces.
4. **A durable record either way**, so "this turn ended without checking" stays an
   observable absence rather than an untestable claim.

NOT enforced, said as plainly: in a project PROVEN to hold no run artifacts, an unbound
session is allowed silently and nothing is observed.  That exemption is deliberate --
gating unrelated sessions is how this hook gets uninstalled, and an uninstalled hook
enforces nothing -- and it is the only one.  It requires proof: a project whose
``artifacts/runs`` this process cannot read is NOT exempt, it is refused.

VERIFIED LIMITATIONS, each one observed rather than assumed:

* **The runtime keeps the last word.**  It overrides a hook that blocks more than
  ``CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`` (default 8) consecutive times and ends the turn.
  This module caps itself LOWER (``STOP_HOOK_BLOCK_CAP_DEFAULT``) so the release is ours,
  tested, and published to the audit as a ``quiescence_violation``.  A boundary that
  could block forever could wedge a live session, which is worse than no boundary.
* **Some turn ends are not blockable.**  The runtime discards a Stop-hook block when the
  turn ended by a tool result, an MCP end-turn or a loop tick with no model re-invoke.
* **Registration is the operator's act, not this repository's.**  Nothing here edits a
  settings file.  An UNREGISTERED session is exactly as ungated as it was before this
  module, and for that case the guarantee remains the one below.  A registered session
  that is not bound to a run is no longer ungated: in a project holding run state -- or
  one whose run state cannot be read -- it is refused until it binds, or until the cap
  releases it.
* **Every refusal is finite.**  Each of the fail-closed paths above -- refusal,
  unreadable authority, unattributable session, unresolvable entry point, internal
  failure -- refuses at most ``STOP_HOOK_BLOCK_CAP_DEFAULT`` consecutive turn ends and
  then releases, saying so.  That bound is what makes blocking an unattributable session
  safe: being wrong about one costs a handful of extra turns, never a wedged session.
  The bound is only real while the count can be RECORDED, so a refusal whose counter
  cannot be persisted is not issued at all -- see the two directions below.

THE TWO DIRECTIONS OF THE FAIL-SAFE, which are deliberately opposite and must not be
"fixed" into each other:

* **Unreadable Run authority -> BLOCK (bounded).**  "I could not look" is not "there is
  nothing there", and letting a turn end unobserved is the OS-44 defect itself.  The
  refusal is safe precisely because it is bounded.
* **Unpersistable block budget -> RELEASE, with an explicit diagnostic.**  The cap exists
  to guarantee escape; a cap that cannot be written down guarantees nothing, because the
  next invocation reads zero and refuses again, forever.  An unbounded block wedges the
  session entirely -- strictly worse than one unobserved turn end -- so when the increment
  cannot be persisted the boundary releases and says why, rather than emitting a refusal
  it cannot bound.  This is not a softening of the rule above: the first direction asks
  what the boundary KNOWS about the run, the second asks whether the boundary can still
  keep its own promise to let go.

When the hook is NOT registered, what stands is what stood before: fail-closed
enforcement at an invocable boundary, plus deterministic after-the-fact detection --
the command is a pure observation of durable state, so anyone (the next turn, a
successor process, a supervising operator, a scheduled check) can run it later against a
quiet run and get the verdict the skipped turn would have got.  That is what
``run_c2166e75bb02`` lacked: its stall was found by a human reading a 33-minute
timestamp gap.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from . import quiescence

try:
    from scripts import run_logging
except ImportError:  # installed Skill layout exposes sibling tools directly
    import run_logging  # type: ignore[no-redef]


#: Same environment override ``OrcaRuntimeHarness`` and ``run_logging`` already honour,
#: so a development build is reached by one variable rather than three conventions.
ORCA_COMMAND_ENV = "ORCA_CLI_COMMAND"

#: Orca's own Task statuses (``orca orchestration task-update --status``).  Spelled here
#: because they are a runtime vocabulary this module reads, not a contract it owns.
TASK_STATUS_DISPATCHED = "dispatched"
TASK_STATUSES_RUNNABLE = ("pending", "ready")
TASK_STATUS_COMPLETED = "completed"

#: Dispatch statuses that mean the Worker/Reviewer is no longer running.  Anything else
#: -- including a status this build has never heard of -- is read as still running,
#: which is the conservative direction for *this* question only in combination with the
#: Task status test below; see :func:`observe_orca_state`.
DISPATCH_FINISHED_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "abandoned", "settled", "released"}
)

#: The runnable-action vocabulary this module reports.  Prefixed so a reader of the
#: audit can tell which kind of work was outstanding without re-deriving it.
ACTION_DISPATCH_TASK = "dispatch_task"
ACTION_COLLECT_WORKER_DONE = "collect_worker_done"
#: A Task Orca still calls ``dispatched`` that NO live worker row corroborates.  Not an
#: active wait -- nothing has been shown to be running -- and not rest.  It is recovery
#: work: the Coordinator has to find out what happened to that Dispatch.
ACTION_RECONCILE_DISPATCH = "reconcile_dispatch"
#: The next node the durable OS-40 checkpoint's own routing produced.  Derived, not
#: declared; see :func:`read_workflow_checkpoint`.
ACTION_CHECKPOINT_ROUTE = "checkpoint_route"
ACTION_DECLARED = "declared_next_node"

#: Where the run status this boundary judged actually came from.  Reported on every
#: observation and in every audit record, so a reader can tell a status DERIVED from
#: durable state apart from one a caller merely declared.
STATUS_AUTHORITY_CHECKPOINT = "workflow_checkpoint"
STATUS_AUTHORITY_PAUSE_RECORD = "os31_pause_record"
STATUS_AUTHORITY_DECLARED = "declared_only"
STATUS_AUTHORITY_NONE = "none"

#: OS-40's durable checkpoint store, at the one path a successor process can find from
#: the run id alone -- ``launcher.resolve_checkpoint_path``'s artifact-root default.
WORKFLOW_CHECKPOINT_FILENAME = ".workflow_checkpoints.json"

#: Where a durable human wait can be proven to exist.  Reported by name so a refusal can
#: say which one was looked for and a pass can say which one was found.
WAIT_EVIDENCE_PAUSE_RECORD = "os31_pause_record"
WAIT_EVIDENCE_CLARIFICATION = "os30_clarification_request"
WAIT_EVIDENCE_DECISION_GATE = "orca_decision_gate"

#: OS-31's durable pause record, read as a closed schema rather than through
#: ``pause_store`` -- the same ~15-line read ``clarification_protocol.run_disposition``
#: already does, and for the same reason: proving a wait exists must not depend on the
#: pause machinery being importable.
PAUSE_RECORD_FILENAME = ".pause_state.json"
PAUSE_RECORD_SCHEMA_VERSION = "os31.pause_record.v2"


class TurnBoundaryUnavailable(RuntimeError):
    """Authoritative state could not be read, so no verdict may be reported.

    Deliberately NOT a refusal verdict.  "The turn may not end" and "I could not find
    out whether the turn may end" are different facts, and collapsing the second into
    the first would let an unreachable runtime read as a well-understood violation.
    Both stop the turn; only one of them names a defect in the run.
    """


def orca_command() -> str:
    return os.environ.get(ORCA_COMMAND_ENV) or shutil.which("orca") or "orca"


def _default_runner(args: Sequence[str]) -> tuple[int, str]:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [orca_command(), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout


def _orca_json(args: Sequence[str], *, runner: Callable[[Sequence[str]], tuple[int, str]]) -> Any:
    """One ``orca ... --json`` call, or :class:`TurnBoundaryUnavailable`."""
    try:
        code, stdout = runner(tuple(args))
    except OSError as error:
        raise TurnBoundaryUnavailable(
            f"`orca {' '.join(args)}` could not be executed ({error}); the turn-end "
            "boundary reports no verdict rather than guessing at run state"
        ) from error
    try:
        payload = json.loads(stdout or "{}")
    except ValueError as error:
        raise TurnBoundaryUnavailable(
            f"`orca {' '.join(args)}` did not return JSON ({error})"
        ) from error
    if code != 0 or not isinstance(payload, dict) or not payload.get("ok"):
        detail = payload.get("error") if isinstance(payload, dict) else stdout
        raise TurnBoundaryUnavailable(
            f"`orca {' '.join(args)}` failed ({detail}); authoritative run state is "
            "unavailable, so no turn-end verdict is reported"
        )
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def _task_dependencies(task: dict[str, Any]) -> tuple[str, ...]:
    """A Task's dependency ids.  Orca stores them as a JSON string in ``deps``."""
    raw = task.get("deps")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            # An unreadable dependency list cannot prove the Task is BLOCKED, and the
            # only direction this boundary may err in is refusing a turn that could have
            # ended.  So it counts as runnable: the Coordinator is asked to look, rather
            # than allowed to leave.
            return ()
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw)


def classify_orca_state(tasks: Any, workers: Any) -> dict[str, Any]:
    """Active dispatches and runnable work, from Orca's own Task and Dispatch records.

    Pure: it takes the two listings and decides, so the CLI boundary and
    ``OrcaRuntimeHarness`` reach the same answer from the same rule rather than each
    spelling it once.

    A Dispatch is ACTIVE only when both authorities agree it is: the Task still carries
    Orca's ``dispatched`` status (so the Coordinator has not recorded a result for it)
    and the Dispatch's worker row does not report a finished state.  When they disagree
    -- a Task still ``dispatched`` whose worker has finished -- that is not rest and it
    is not an active wait either: it is a ``worker_done`` waiting to be collected, so it
    is reported as runnable WORK.  That disagreement is exactly the moment
    ``run_c2166e75bb02`` ended its turn in.

    Neither source is the settlement ledger, and that is the point of this function.  A
    Dispatch enters that ledger through ``claim_settlement()`` only after
    ``wait_for_done()`` has returned, so it can only ever describe a Worker or Reviewer
    that has already finished -- never one that is currently running.

    A ``dispatched`` Task that NO live worker row corroborates is reported as recovery
    work (``ACTION_RECONCILE_DISPATCH``), never as an active wait.  The previous round
    read a missing row as active on the grounds that a build without a worker listing
    would otherwise have every legitimate wait refused; that is precisely the fail-open
    shape OS-44 exists to remove -- "I could not prove a Worker is running" became "a
    Worker is running", and one stale Task status was then enough to call a dead run
    quiescent.  Absence of evidence fails closed here: the turn is refused and the
    Coordinator is told to find out what happened to that Dispatch.  Activity is
    reported only when both authorities agree, which is exactly what the contract and
    the SKILL.md prose have always said.
    """
    tasks = [task for task in (tasks or []) if isinstance(task, dict)]
    workers = [worker for worker in (workers or []) if isinstance(worker, dict)]

    finished_dispatches = set()
    live_dispatches = set()
    dispatch_of_task: dict[str, str] = {}
    for worker in workers:
        dispatch_id = str(worker.get("dispatchId") or "")
        if not dispatch_id:
            continue
        task_id = str(worker.get("taskId") or "")
        if task_id:
            dispatch_of_task[task_id] = dispatch_id
        status = str(worker.get("dispatchStatus") or "")
        state = str(worker.get("workerState") or "")
        if status in DISPATCH_FINISHED_STATUSES or state in DISPATCH_FINISHED_STATUSES:
            finished_dispatches.add(dispatch_id)
        else:
            live_dispatches.add(dispatch_id)

    status_of = {str(task.get("id")): str(task.get("status") or "") for task in tasks}
    active: list[str] = []
    actions: list[str] = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        status = str(task.get("status") or "")
        if status == TASK_STATUS_DISPATCHED:
            dispatch_id = str(task.get("dispatch_id") or dispatch_of_task.get(task_id, ""))
            if dispatch_id and dispatch_id in live_dispatches:
                active.append(dispatch_id)
            elif dispatch_id and dispatch_id in finished_dispatches:
                actions.append(f"{ACTION_COLLECT_WORKER_DONE}:{task_id}")
            else:
                # No live worker row proves anything is running for this Task, so the
                # turn is refused rather than credited with a wait it cannot show.
                actions.append(f"{ACTION_RECONCILE_DISPATCH}:{task_id}")
            continue
        if status not in TASK_STATUSES_RUNNABLE:
            continue
        blockers = [
            dependency
            for dependency in _task_dependencies(task)
            if status_of.get(dependency) != TASK_STATUS_COMPLETED
        ]
        if not blockers:
            actions.append(f"{ACTION_DISPATCH_TASK}:{task_id}")
    return {
        "active_dispatches": sorted(active),
        "runnable_actions": sorted(actions),
        "task_count": len(tasks),
        "worker_count": len(workers),
    }


def observe_orca_state(
    run_id: str, *, runner: Callable[[Sequence[str]], tuple[int, str]]
) -> dict[str, Any]:
    """:func:`classify_orca_state` over the two live listings for one Run."""
    return classify_orca_state(
        _orca_json(
            ("orchestration", "task-list", "--run", run_id, "--json"), runner=runner
        ).get("tasks"),
        _orca_json(
            ("orchestration", "worker-list", "--run", run_id, "--json"), runner=runner
        ).get("workers"),
    )


def observe_deliveries(run_id: str, *, artifact_base: Path) -> dict[str, Any]:
    """Every delivery obligation the run's own durable audit still carries.

    Reads the append-only ``coordinator_audit/`` rather than any process's memory,
    because the whole point of a turn-end boundary is that it can be answered by a
    process that did not do the work.  An audit that cannot be read fails closed: it is
    the only source that distinguishes an already-handled delivery from a new one.

    Every open obligation counts here.  ``OrcaRuntimeHarness.unacknowledged_deliveries``
    stands aside for the two a redelivery closes, and it may: it knows which rows it
    consumed itself, so it can tell "my acknowledgement" from "a predecessor's, which
    arrives on the waiter I am about to arm".  This function is reading someone else's
    audit and has no such knowledge, and a turn with any delivery obligation open is not
    at rest under either reading, so it refuses rather than guesses.
    """
    try:
        ledger = run_logging.replay_delivery_ledger(run_id, base=artifact_base)
    except (OSError, run_logging.CoordinatorAuditError, ValueError) as error:
        raise TurnBoundaryUnavailable(
            f"the coordinator audit for run {run_id} could not be read ({error}); it is "
            "the only source of the run's delivery obligations, so no turn-end verdict "
            "is reported"
        ) from error
    return {
        "outstanding": [
            delivery_id
            for delivery_id, row in sorted(ledger.items())
            if quiescence.delivery_obligation(row) != quiescence.OBLIGATION_NONE
        ]
    }


def classify_checkpoint_state(
    state: dict[str, Any], *, route: Callable[[dict[str, Any]], str]
) -> tuple[str, str]:
    """One committed ``WorkflowState`` as ``(run status, next node)``.  Pure.

    Both halves come out of the checkpoint itself: the status from ``terminal_status``
    (a run that ended says so) or the OS-31 ``run_lifecycle``, and the next node from
    the engine's own single routing decision, ``routing.route`` -- the same function the
    graph would evaluate on its next step.  Nothing here is a caller's word for it.

    A run whose lifecycle is already ``WAITING_FOR_INPUT`` reports no next node: the
    route token for such a state is ``PAUSE``, which re-enters the pause it is already
    in, and reporting it as runnable work would describe a legitimately paused run as
    outstanding work.
    """
    terminal = state.get("terminal_status")
    if terminal:
        return str(terminal), ""
    lifecycle = str(state.get("run_lifecycle") or "")
    if lifecycle == quiescence.WAITING_FOR_INPUT:
        return quiescence.WAITING_FOR_INPUT, ""
    return lifecycle or "ACTIVE", str(route(state) or "")


def workflow_checkpoint_path(
    run_id: str, *, artifact_base: Path, explicit: Any = None
) -> Path:
    """Where this run's durable OS-40 checkpoint store lives.

    The artifact-root default is ``launcher.resolve_checkpoint_path``'s, and it is the
    only one addressable from a run id alone; ``--checkpoint-store`` names a store kept
    somewhere else.  The ``ORCA_OS40_CHECKPOINT_DIR`` layout is deliberately NOT guessed
    at here, because its filename needs a thread id this boundary does not have -- an
    operator who moved the store passes the path.
    """
    if explicit:
        return Path(explicit)
    return Path(artifact_base) / "artifacts" / "runs" / run_id / WORKFLOW_CHECKPOINT_FILENAME


def read_workflow_checkpoint(
    run_id: str, *, artifact_base: Path, explicit: Any = None
) -> dict[str, Any]:
    """Run status and graph next node from the run's own durable checkpoint, if it has one.

    This is the authoritative answer to "what state is the run in and what does the
    graph owe next?", and it is read from the OS-40 checkpoint store rather than taken
    from the caller: a prompt-driven Coordinator's ``--declare`` is a sentence a model
    typed, and the whole point of the boundary is not to grade a turn on its own
    account of itself.

    Fail-closed in both directions that matter.  A store that exists but cannot be
    opened, deserialised or validated raises :class:`TurnBoundaryUnavailable` -- an
    unreadable authority is never an absent one.  Threads that disagree about the run's
    status also raise, because guessing which one speaks for the run is exactly the kind
    of inference this boundary exists to refuse.

    A run with NO checkpoint store is a real and normal case, and it is reported as
    such (``present`` False) rather than invented: the prompt-driven Coordinator this
    skill orchestrates does not execute the LangGraph engine at all.  What the caller
    then does with that fact is stated at :func:`observe` -- Orca Task state, the OS-31
    pause record and the run's audit remain authoritative, and the run STATUS is a
    declaration the boundary can only corroborate, never verify.
    """
    path = workflow_checkpoint_path(run_id, artifact_base=artifact_base, explicit=explicit)
    absent = {"present": False, "run_status": "", "next_nodes": (), "threads": ()}
    if not path.is_file():
        return absent
    try:
        from . import routing
        from .checkpoint_store import CheckpointStoreError, FileCheckpointSaver
        from .pause_runtime import restore_closed_state
        from .state import StateError, validate_state
    except ImportError as error:  # LangGraph absent: the authority cannot be read
        raise TurnBoundaryUnavailable(
            f"run {run_id} has a durable workflow checkpoint at {path} but it cannot be "
            f"opened ({error}); an unreadable authority is not an absent one, so no "
            "turn-end verdict is reported"
        ) from error
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        threads = document.get("threads") if isinstance(document, dict) else None
        if not isinstance(threads, dict):
            raise ValueError("the checkpoint store holds no thread index")
        saver = FileCheckpointSaver(path)
        statuses: set[str] = set()
        next_nodes: set[str] = set()
        seen: list[str] = []
        for thread_id, thread in sorted(threads.items()):
            if not isinstance(thread, dict) or thread.get("retired"):
                continue
            namespaces = thread.get("namespaces")
            for namespace in sorted(namespaces if isinstance(namespaces, dict) else {}):
                head = saver.head(str(thread_id), checkpoint_ns=str(namespace))
                if not head:
                    continue
                committed = saver.get_tuple(
                    {
                        "configurable": {
                            "thread_id": str(thread_id),
                            "checkpoint_ns": str(namespace),
                            "checkpoint_id": head,
                        }
                    }
                )
                if committed is None:
                    continue
                state = dict(
                    validate_state(
                        restore_closed_state(
                            committed.checkpoint.get("channel_values") or {}
                        ),
                        expected_thread_id=str(thread_id),
                    )
                )
                if str(state.get("run_id") or "") != run_id:
                    continue
                seen.append(str(thread_id))
                status, next_node = classify_checkpoint_state(state, route=routing.route)
                statuses.add(status)
                if next_node:
                    next_nodes.add(next_node)
    except (CheckpointStoreError, StateError, OSError, ValueError, KeyError, TypeError) as error:
        raise TurnBoundaryUnavailable(
            f"the durable workflow checkpoint for run {run_id} at {path} could not be "
            f"read ({error}); it is this run's authority for run status and next node, "
            "so no turn-end verdict is reported"
        ) from error
    if not seen:
        return absent
    if len(statuses) > 1:
        raise TurnBoundaryUnavailable(
            f"the durable workflow checkpoint for run {run_id} holds live threads that "
            f"disagree about the run's status ({sorted(statuses)} across {sorted(seen)}); "
            "the boundary does not pick one, so no turn-end verdict is reported"
        )
    return {
        "present": True,
        "run_status": statuses.pop(),
        "next_nodes": tuple(sorted(next_nodes)),
        "threads": tuple(sorted(seen)),
    }


def pause_record_status(run_id: str, *, artifact_base: Path) -> str:
    """The run's OS-31 durable lifecycle status, or ``""`` when there is no record.

    Read as a closed schema rather than through ``pause_store``, exactly as
    ``clarification_protocol.run_disposition`` does and for the same reason: the
    turn-end boundary has to be answerable without the pause machinery being importable.
    This is the one run-status source that is an ARTEFACT rather than a claim, so it is
    what the boundary falls back to when the caller declares nothing.
    """
    record_path = Path(artifact_base) / "artifacts" / "runs" / run_id / PAUSE_RECORD_FILENAME
    try:
        document = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(document, dict) or document.get("schema_version") != PAUSE_RECORD_SCHEMA_VERSION:
        return ""
    record = document.get("record")
    if not isinstance(record, dict) or record.get("run_id") != run_id:
        return ""
    status = record.get("status")
    return status if isinstance(status, str) else ""


def observe_durable_wait(
    run_id: str,
    *,
    artifact_base: Path,
    runner: Callable[[Sequence[str]], tuple[int, str]],
) -> tuple[str, ...]:
    """Which durable artefacts prove a human wait is actually armed for this run."""
    evidence: list[str] = []
    if pause_record_status(run_id, artifact_base=artifact_base) == quiescence.WAITING_FOR_INPUT:
        evidence.append(WAIT_EVIDENCE_PAUSE_RECORD)
    requests = (
        Path(artifact_base) / "artifacts" / "runs" / run_id / "clarifications" / "requests"
    )
    try:
        published = any(child.is_dir() for child in requests.iterdir())
    except OSError:
        published = False
    if published:
        evidence.append(WAIT_EVIDENCE_CLARIFICATION)
    try:
        gates = _orca_json(
            ("orchestration", "gate-list", "--run", run_id, "--json"), runner=runner
        ).get("gates")
    except TurnBoundaryUnavailable:
        # A gate listing this build cannot produce is not evidence either way, and it
        # must not stop a verdict the other two sources can already support.
        gates = None
    for gate in gates or []:
        if isinstance(gate, dict) and str(gate.get("status") or "") not in {
            "resolved",
            "cancelled",
        }:
            evidence.append(WAIT_EVIDENCE_DECISION_GATE)
            break
    return tuple(evidence)


def observe(
    run_id: str,
    *,
    artifact_base: Path | str = ".",
    declared_status: str = "",
    declared_next_node: str = "",
    checkpoint_store: Any = None,
    runner: Callable[[Sequence[str]], tuple[int, str]] | None = None,
) -> dict[str, Any]:
    """Everything the turn-end verdict needs, gathered in one atomic invocation.

    ``declared_status`` and ``declared_next_node`` are the only inputs the caller
    supplies, and neither can make the verdict more permissive: a declared next node is
    ADDED to the runnable work, and a declared rest state is either contradicted by
    authoritative state (refused) or merely corroborated by it (never trusted).

    RUN STATUS AND NEXT NODE, AND WHERE THEY COME FROM
    --------------------------------------------------
    In priority order, and reported as ``status_authority`` so the answer always says
    which one it used:

    1. ``workflow_checkpoint`` -- the run's durable OS-40 checkpoint store.  Run status
       comes from the committed ``terminal_status`` / ``run_lifecycle`` and the next
       node from the engine's own ``routing.route``.  Both are derived.
    2. ``os31_pause_record`` -- the durable pause artefact, for a run that paused.  A
       real artefact, so a legitimately paused run is not misread as an idle ACTIVE one.
    3. ``declared_only`` -- neither exists.  This is the ordinary case for the
       prompt-driven Coordinator, which never executes the LangGraph engine: no
       checkpoint is written for its run, so there IS no durable run-status authority
       for it.  The boundary says so rather than dressing the declaration up as a
       derivation.  Everything else it judges is still derived -- active dispatch and
       runnable work from Orca's Task and Dispatch records, obligations from the run's
       audit, the wait from a durable artefact -- and those are what a declared rest
       state is corroborated against.

    A declaration that CONTRADICTS an authoritative status is a conflict, reported here
    and refused in :func:`verdict_for`.  It can never win: a Coordinator that has
    genuinely blocked or escalated a run records that in the run's durable state first.
    """
    base = Path(artifact_base)
    call = runner or _default_runner
    orca_state = observe_orca_state(run_id, runner=call)
    deliveries = observe_deliveries(run_id, artifact_base=base)
    checkpoint = read_workflow_checkpoint(run_id, artifact_base=base, explicit=checkpoint_store)
    actions = list(orca_state["runnable_actions"])
    actions.extend(f"{ACTION_CHECKPOINT_ROUTE}:{node}" for node in checkpoint["next_nodes"])
    if declared_next_node:
        actions.append(f"{ACTION_DECLARED}:{declared_next_node}")
    wait_evidence = observe_durable_wait(run_id, artifact_base=base, runner=call)
    pause_status = pause_record_status(run_id, artifact_base=base)
    if checkpoint["present"]:
        observed_status, authority = checkpoint["run_status"], STATUS_AUTHORITY_CHECKPOINT
    elif pause_status:
        observed_status, authority = pause_status, STATUS_AUTHORITY_PAUSE_RECORD
    else:
        observed_status = ""
        authority = STATUS_AUTHORITY_DECLARED if declared_status else STATUS_AUTHORITY_NONE
    next_node = checkpoint["next_nodes"][0] if checkpoint["next_nodes"] else ""
    return {
        "run_id": run_id,
        "run_status": observed_status or declared_status or "ACTIVE",
        "declared_status": declared_status,
        "observed_status": observed_status,
        "status_authority": authority,
        "status_conflict": bool(
            declared_status and observed_status and declared_status != observed_status
        ),
        "next_node": next_node,
        "checkpoint_threads": list(checkpoint["threads"]),
        "active_dispatches": list(orca_state["active_dispatches"]),
        "runnable_actions": sorted(actions),
        "outstanding_deliveries": list(deliveries["outstanding"]),
        "durable_wait_evidence": list(wait_evidence),
        "task_count": orca_state["task_count"],
        "worker_count": orca_state["worker_count"],
    }


def verdict_for(observation: dict[str, Any]) -> dict[str, Any]:
    """The pure contract's judgement over one :func:`observe` result.

    The conflict clause below can only ever DOWNGRADE the contract's verdict, exactly as
    every other check in this module can: a turn whose declared rest state disagrees
    with the run's own durable state is refused, and no declaration ever clears a turn
    the contract refused.
    """
    verdict = quiescence.turn_end_verdict(
        run_status=observation["run_status"],
        next_node=observation.get("next_node", ""),
        active_dispatches=len(observation["active_dispatches"]),
        unacknowledged_deliveries=observation["outstanding_deliveries"],
        runnable_actions=observation["runnable_actions"],
        durable_wait_armed=bool(observation["durable_wait_evidence"]),
    )
    if observation.get("status_conflict") and verdict["quiescent"]:
        verdict["quiescent"] = False
        verdict["reason_code"] = quiescence.QUIESCENCE_UNSUPPORTED_REST_CLAIM
        verdict["detail"] = (
            f"the turn declares {observation['declared_status']} but the run's own "
            f"{observation['status_authority']} says {observation['observed_status']}; "
            "the durable state is the authority, so the declaration is refused rather "
            "than accepted over it"
        )
    return verdict


def record_verdict(
    run_id: str,
    verdict: dict[str, Any],
    observation: dict[str, Any],
    *,
    artifact_base: Path,
    source: str = "turn_boundary_cli",
) -> None:
    """Publish the verdict to the run's own append-only Coordinator audit.

    Durable on purpose: "this turn ended without checking" has to be an observable
    ABSENCE in the run's evidence, and an absence is only observable when every check
    that did happen left a record.
    """
    run_logging.append_coordinator_audit_record(
        run_id,
        run_logging.EVENT_QUIESCENCE_VERIFIED
        if verdict["quiescent"]
        else run_logging.EVENT_QUIESCENCE_VIOLATION,
        {
            "run_status": observation["run_status"],
            "status_authority": observation["status_authority"],
            "next_node": verdict.get("next_node", ""),
            "active_dispatches": len(observation["active_dispatches"]),
            "reason_code": verdict["reason_code"],
            "detail": verdict["detail"],
            "delivery_id": (observation["outstanding_deliveries"] or [""])[0],
            "source": source,
        },
        base=artifact_base,
    )


def enforce(
    run_id: str,
    *,
    artifact_base: Path | str = ".",
    declared_status: str = "",
    declared_next_node: str = "",
    checkpoint_store: Any = None,
    runner: Callable[[Sequence[str]], tuple[int, str]] | None = None,
    source: str = "turn_boundary_cli",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Observe, judge, record.  Returns ``(verdict, observation)``.

    Raises :class:`TurnBoundaryUnavailable` when authoritative state could not be read,
    and also when a PASSING verdict could not be published -- an unrecorded pass is
    indistinguishable from a turn that never checked, and this boundary's whole detection
    story rests on that distinction.  A refusal that could not be published is still a
    refusal, so it is returned rather than raised.
    """
    base = Path(artifact_base)
    observation = observe(
        run_id,
        artifact_base=base,
        declared_status=declared_status,
        declared_next_node=declared_next_node,
        checkpoint_store=checkpoint_store,
        runner=runner,
    )
    verdict = verdict_for(observation)
    try:
        record_verdict(run_id, verdict, observation, artifact_base=base, source=source)
    except (OSError, run_logging.RunLoggingError) as error:
        if verdict["quiescent"]:
            raise TurnBoundaryUnavailable(
                f"the turn-end verdict for run {run_id} could not be published "
                f"({error}); an unrecorded pass is indistinguishable from a turn that "
                "never checked, so the turn is not cleared to end"
            ) from error
    return verdict, observation


# ---- the CLI verb ------------------------------------------------------------------
#: The command exited having proven the turn may end.
EXIT_QUIESCENT = 0
#: The command proved the turn may NOT end.  A refusal, not an error.
EXIT_REFUSED = 1
#: Authoritative state was unavailable, so no verdict was reached.
EXIT_UNAVAILABLE = 3


def run_turn_boundary_cli(
    args: Any, *, runner: Callable[[Sequence[str]], tuple[int, str]] | None = None
) -> int:
    """``run_workflow.py turn-end``: derive the state, refuse or clear the turn end."""
    base = Path(args.artifact_base)
    try:
        verdict, observation = enforce(
            args.run_id,
            artifact_base=base,
            declared_status=args.declare,
            declared_next_node=args.next_node,
            checkpoint_store=getattr(args, "checkpoint_store", None),
            runner=runner,
        )
    except TurnBoundaryUnavailable as error:
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "run_id": args.run_id,
                        "quiescent": False,
                        "reason_code": "TURN_BOUNDARY_UNAVAILABLE",
                        "detail": str(error),
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                )
            )
        else:
            print(f"turn-end: {error}")
        return EXIT_UNAVAILABLE
    summary = {
        "run_id": args.run_id,
        "quiescent": verdict["quiescent"],
        "state": verdict["state"],
        "reason_code": verdict["reason_code"],
        "detail": verdict["detail"],
        "run_status": observation["run_status"],
        "status_authority": observation["status_authority"],
        "active_dispatches": observation["active_dispatches"],
        "runnable_actions": observation["runnable_actions"],
        "outstanding_deliveries": observation["outstanding_deliveries"],
        "durable_wait_evidence": observation["durable_wait_evidence"],
    }
    if getattr(args, "json", False):
        print(json.dumps(summary, sort_keys=True, ensure_ascii=False))
    elif verdict["quiescent"]:
        print(
            f"turn-end: run={args.run_id} state={verdict['state']} "
            "the turn may end"
        )
    else:
        print(
            f"turn-end: REFUSED run={args.run_id} {verdict['reason_code']}: "
            f"{verdict['detail']}"
        )
    return EXIT_QUIESCENT if verdict["quiescent"] else EXIT_REFUSED


# ---- the Stop-hook boundary ---------------------------------------------------------
# FINAL-R1.  ``turn-end`` above is invoked by whoever remembers to invoke it.  This verb
# is the same derivation wired to the one place the installed runtime calls out to when
# a turn is actually ending, so that "the Coordinator forgot" stops being a live hole.
#
# WHAT WAS VERIFIED ABOUT THAT RUNTIME, AND HOW
# ---------------------------------------------
# Observed on 2026-09-06 against the installed Claude Code 2.1.260
# (`~/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`) and the
# live `~/.claude/settings.json`:
#
# * the settings file registers hooks for, among others, the ``Stop`` event, and Orca's
#   own `~/.orca/agent-hooks/claude-hook.sh` is already one of them;
# * the binary evaluates Stop hooks from a query site labelled ``blockable_turn_end``,
#   turns a hook's ``blockingError`` into a message pushed onto the conversation and
#   re-invokes the model rather than ending the turn;
# * it caps that: ``CLAUDE_CODE_STOP_HOOK_BLOCK_CAP ?? 8`` consecutive blocks, after
#   which it emits "A hook blocked the turn from ending N consecutive times --
#   overriding and ending turn" and ends the turn anyway;
# * it passes ``stop_hook_active`` in the hook's stdin payload and instructs hooks to
#   "return success while it's true";
# * a block is DISCARDED when the turn ended by a tool result, an MCP end-turn or a loop
#   tick with no model re-invoke (``[end-turn] Stop hook block discarded``).
#
# So a Stop hook is a real, blocking, automatic turn-end interception point, and the
# earlier claim that no such hook exists was wrong.  What it is NOT is unbounded: the
# runtime always keeps the last word.  Both facts are stated where the scope is claimed.
#
# WHAT THIS VERB WILL NOT DO
# --------------------------
# It never edits a settings file, and nothing in this repository installs it.  A hook
# that blocks wrongly is worse than no hook -- it can wedge a live session -- so
# registration is an explicit, documented, opt-in act by the operator.  What it gates is
# decided by the project, not only by the binding: a session bound to a Run is gated on
# that Run; an UNBOUND session is BLOCKED, up to the cap, whenever the project holds Run
# state or that Run state cannot be read, and is allowed silently only where the project
# is proven to hold none.  An earlier round merely TOLD an unbound session it was
# ungated and let the turn end, which is the defect this round exists to fix: an allow
# does not re-invoke the model, so an announcement reaches nobody who could act on it.
#
# HOW THE HOOK FINDS THE RUN (BUGFIX-I4-R1-REAL-PATH)
# ---------------------------------------------------
# The first version of this wiring documented a registration that assigned
# ``ORCA_QUIESCENCE_RUN_ID`` to its own value and hoped a parent process had set it.
# Nothing in this repository -- or anywhere else -- ever produced that variable, so the
# advertised registration was inert by construction.  The binding is now something this
# repository PRODUCES, from a value the platform really supplies:
#
# * Claude Code exports ``CLAUDE_CODE_SESSION_ID`` into every command it runs (observed
#   in this process's own environment on 2026-09-06, Claude Code 2.1.260) and sends the
#   same value as ``session_id`` in the Stop hook payload.
# * So a Coordinator binds its Run once, in its own shell, with
#   ``run_workflow.py turn-end-bind --run-id RUN_ID`` -- and ``OrcaRuntimeHarness``
#   does it automatically in ``start_run``/``resume_run`` -- which writes a durable
#   record under the run's own artifact directory.
# * The Stop hook reads its payload's ``session_id`` and finds that record.  Nobody has
#   to remember to export anything, and the binding survives every later turn of the
#   session, a compaction, and a successor process that resumes the same Run.
#
# ``--run-id`` on the registration and ``ORCA_QUIESCENCE_RUN_ID`` in the environment
# still win, in that order, for an operator who wants one specific run pinned.

#: How the hook learns which Run this session's Coordinator is driving, when the
#: operator pins one explicitly.  The durable session binding below is what makes the
#: ordinary case work without it.
STOP_HOOK_RUN_ENV = "ORCA_QUIESCENCE_RUN_ID"
#: The session id Claude Code exports into every command it runs AND sends as
#: ``session_id`` in the hook payload.  That those are the same value is the whole
#: mechanism: it lets a record written by the Coordinator's shell be found by the Stop
#: hook the runtime fires for that same session.
SESSION_ID_ENV = "CLAUDE_CODE_SESSION_ID"
#: Where the binding lives -- beside the run's other durable state, one file per
#: session, so a run bound by several successive Coordinator processes keeps all of them
#: and the hook picks the one that is still open.
COORDINATOR_SESSION_DIRNAME = "coordinator_session"
SESSION_BINDING_SCHEMA = "os44.coordinator_session_binding.v1"
#: This boundary's OWN consecutive-block cap, deliberately below the runtime's default 8
#: so the escape hatch is ours and is exercised by our tests rather than inherited from a
#: version we do not control.
STOP_HOOK_CAP_ENV = "ORCA_QUIESCENCE_STOP_HOOK_BLOCK_CAP"
STOP_HOOK_BLOCK_CAP_DEFAULT = 3
#: Per-session consecutive-block counters, beside the run's other durable state.
STOP_HOOK_STATE_FILENAME = ".stop_hook_blocks.json"
#: The same counter for a session this boundary could NOT attribute to a Run.  It has no
#: run directory to live in, so it lives beside them, at the project's ``artifacts/runs``
#: root -- or, when that root is the very thing that cannot be read, at the first
#: enclosing directory that will take it.  A regular file in either place is not a run
#: directory, so it cannot make ``project_run_state()`` answer differently than it did
#: before this file existed.
STOP_HOOK_UNBOUND_STATE_FILENAME = ".stop_hook_unbound_blocks.json"
#: The counter the REGISTRATION ITSELF keeps, in the shell, for the case where no entry
#: point resolves and this module therefore never runs.  Plain text with one integer,
#: because the only thing that can write it there is ``printf``.
STOP_HOOK_UNRESOLVED_STATE_FILENAME = ".stop_hook_unresolved_blocks"
#: ``source`` on the audit record, so a reader can tell an automatic Stop-hook verdict
#: from one a caller invoked by hand.
STOP_HOOK_SOURCE = "turn_boundary_stop_hook"
#: The same, for the turn the cap released despite a refusal.  Distinct on purpose: it
#: is the one shape in which this boundary knowingly lets a forbidden turn end.
STOP_HOOK_SOURCE_RELEASED = "turn_boundary_stop_hook_cap_released"
#: The Stop hook's own exit code.  Always 0: the verdict travels in the JSON on stdout,
#: and a non-zero exit would be read by the runtime as the hook itself failing.
EXIT_STOP_HOOK = 0


def stop_hook_block_cap(env: Any = None) -> int:
    """This boundary's consecutive-block budget for one stop chain."""
    raw = (env if env is not None else os.environ).get(STOP_HOOK_CAP_ENV) or ""
    try:
        cap = int(raw)
    except (TypeError, ValueError):
        return STOP_HOOK_BLOCK_CAP_DEFAULT
    return cap if cap >= 0 else STOP_HOOK_BLOCK_CAP_DEFAULT


def read_stop_hook_payload(stream: Any) -> dict[str, Any]:
    """The Stop hook's stdin document, or ``{}``.

    A payload this build cannot parse is not an occasion to block: the hook has learned
    nothing about the run, and blocking on nothing is how a hook wedges a session.
    """
    try:
        raw = stream.read()
    except (OSError, ValueError):
        return {}
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _runs_root(artifact_base: Any) -> Path:
    return Path(artifact_base) / "artifacts" / "runs"


def session_key(session_id: str) -> str:
    """A filesystem-safe name for a session id.

    Claude Code's session ids are UUIDs, but this names a file derived from a value
    another process supplies, so it is constrained here rather than trusted.
    """
    return re.sub(r"[^A-Za-z0-9_.-]", "_", (session_id or "").strip())[:128]


def current_session_id(env: Any = None) -> str:
    """This process's Claude Code session, when it is running inside one."""
    return ((env if env is not None else os.environ).get(SESSION_ID_ENV) or "").strip()


def session_binding_path(run_id: str, session_id: str, *, artifact_base: Any = ".") -> Path:
    return (
        _runs_root(artifact_base)
        / run_id
        / COORDINATOR_SESSION_DIRNAME
        / f"{session_key(session_id)}.json"
    )


def bind_session_run(
    run_id: str,
    *,
    session_id: str = "",
    artifact_base: Any = ".",
    env: Any = None,
    release: bool = False,
) -> Path | None:
    """Record that this Claude Code session is driving ``run_id``; return the record.

    This is the producer the previous round did not have.  It writes one small durable
    file under the run's own artifact directory, which the Stop hook -- fired by the
    runtime for this same session, carrying this same session id -- reads to learn which
    Run it is gating.

    Returns ``None`` and changes nothing when there is no session to bind (the ordinary
    case outside Claude Code) or the record cannot be written: a Coordinator's run must
    not fail because a convenience binding could not be published.  The consequence of a
    missing binding is never a hook that guesses -- it is a hook that BLOCKS the session's
    turn ends, up to its cap, for as long as this project holds Run state or its Run state
    cannot be read, and passes them over in silence only where the project is proven to
    hold none.
    """
    session = session_id or current_session_id(env)
    if not run_id or not session:
        return None
    path = session_binding_path(run_id, session, artifact_base=artifact_base)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # A release keeps the moment the session took the run, so the record reads as the
    # interval it actually was rather than collapsing to the instant it ended.
    bound_at = stamp
    if release:
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        if isinstance(previous, dict) and previous.get("bound_at"):
            bound_at = str(previous["bound_at"])
    document = {
        "schema": SESSION_BINDING_SCHEMA,
        "run_id": run_id,
        "session_id": session,
        "bound_at": bound_at,
        "released_at": stamp if release else None,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    except OSError:
        return None
    return path


def release_session_run(
    run_id: str, *, session_id: str = "", artifact_base: Any = ".", env: Any = None
) -> Path | None:
    """Stop gating this session on ``run_id``.

    A Coordinator that has finished with a Run releases it, so a later session on the
    same machine is not blocked on a run it is not driving.  Releasing is a written
    record rather than a deleted file: "this session let go of that run at 12:04" is a
    fact a reader of the run's artifacts can see.
    """
    return bind_session_run(
        run_id, session_id=session_id, artifact_base=artifact_base, env=env, release=True
    )


def begin_run_liveness(
    run_id: str, *, session_id: str = "", artifact_base: Any = ".", env: Any = None,
    lease_seconds: float | None = None, waiter: Any = None
) -> Any | None:
    """Start publishing this Coordinator's OS-43 liveness lease for ``run_id``.

    ADDITIVE.  It wraps ``coordinator_liveness.begin_coordinator_liveness`` and nothing
    else: the session binding above is unchanged, keeps its own schema and gains no field,
    so nothing that reads the binding sees a new one.  Liveness is a SEPARATE record for
    the same reason the binding is separate from the pause record -- a binding is a record
    of INTENT, and liveness is a record of a beating heart.

    Returns ``None`` and changes nothing when there is no session to bind or the record
    cannot be published, exactly as :func:`bind_session_run` does: a Coordinator's run must
    not fail because a liveness record could not be written.  The consequence of a missing
    record is never a Watchdog that guesses -- it is the four-valued ``ABSENT`` read, which
    DECLINES.
    """
    from .coordinator_liveness import begin_coordinator_liveness
    from .runtime_state import DEFAULT_LEASE_SECONDS
    session = session_id or current_session_id(env)
    if not run_id:
        return None
    return begin_coordinator_liveness(
        run_id, artifact_base=artifact_base, session_id=session,
        lease_seconds=DEFAULT_LEASE_SECONDS if lease_seconds is None
        else float(lease_seconds), waiter=waiter)


def end_run_liveness(keeper: Any, run_id: str, *, artifact_base: Any = ".") -> bool:
    """Retire the liveness keeper and record the release.  True on a clean shutdown.

    ADDITIVE, and the mirror of :func:`begin_run_liveness`.  Releasing is a WRITTEN record
    rather than a deleted file, for the same reason :func:`release_session_run` is.
    """
    from .coordinator_liveness import end_coordinator_liveness
    return end_coordinator_liveness(keeper, run_id, artifact_base=artifact_base)


def session_bound_run_id(session_id: str, *, artifact_base: Any = ".") -> str:
    """The Run this session bound most recently and has not released, or ``""``.

    Deterministic when a session has bound several runs: the newest ``bound_at`` wins,
    ties broken on the run id, so two readers of the same artifacts always agree.
    """
    key = session_key(session_id)
    if not key:
        return ""
    try:
        candidates = sorted(
            _runs_root(artifact_base).glob(f"*/{COORDINATOR_SESSION_DIRNAME}/{key}.json")
        )
    except OSError:
        return ""
    best = ("", "")
    for path in candidates:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(document, dict) or document.get("released_at"):
            continue
        entry = (str(document.get("bound_at") or ""), str(document.get("run_id") or ""))
        if entry[1] and entry >= best:
            best = entry
    return best[1]


#: The three answers ``project_run_state()`` can give, and the reason it is not a
#: boolean.  FINAL attempt-3 R1: the previous predicate caught every ``OSError`` from
#: iterating ``artifacts/runs`` and answered ``False``, which made "the authority is
#: unreadable" indistinguishable from "there is positively nothing here" -- and only the
#: second of those is evidence that an unbound session may end its turn in silence.
#: Reproduced against the shipped code: with ``artifacts/runs/run_x`` present and the
#: runs root at mode 000, the predicate answered ``False`` and the hook answered
#: ``{"suppressOutput": true}``.
RUN_STATE_PRESENT = "runs_present"
RUN_STATE_PROVEN_ABSENT = "proven_absent"
RUN_STATE_UNREADABLE = "unreadable"


def project_run_state(artifact_base: Any = ".") -> str:
    """Whether this project holds Orca run artifacts -- or whether that is unknowable.

    The one thing that separates "a session that has nothing to do with this" from "a
    Coordinator that forgot to bind its Run", and therefore whether an unbound session is
    passed over in silence or refused until it binds.  Because that judgement is what
    licenses the boundary's ONLY silent allow, it answers three ways rather than two:

    * ``RUN_STATE_PRESENT`` -- a run directory was positively observed.
    * ``RUN_STATE_PROVEN_ABSENT`` -- the runs root was read and holds no run directory,
      or it does not exist and the directory that would contain it was readable enough to
      establish that.  This is the only positive evidence of irrelevance there is.
    * ``RUN_STATE_UNREADABLE`` -- the authority could not be read: the runs root, or a
      directory on the way to it, refused to be listed or stat'ed, or something that is
      not a directory sits where it should be.  "I could not look" is not "there is
      nothing there", and this boundary fails closed on it exactly as it does on
      unreadable run state, bounded by the same consecutive-block cap.

    ``os.scandir`` rather than ``Path.iterdir``/``Path.is_dir``: ``Path.is_dir()``
    swallows the ``OSError`` from a failed stat and answers ``False``, which is the same
    conflation one level down -- a runs root that can be listed but not searched would
    otherwise read as empty.
    """
    root = _runs_root(artifact_base)
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                try:
                    if entry.is_dir():
                        return RUN_STATE_PRESENT
                except OSError:
                    return RUN_STATE_UNREADABLE
    except FileNotFoundError:
        return RUN_STATE_PROVEN_ABSENT
    except OSError:
        return RUN_STATE_UNREADABLE
    return RUN_STATE_PROVEN_ABSENT


def project_has_runs(artifact_base: Any = ".") -> bool:
    """Was a run directory positively observed in this project?

    A narrow, positive question, and deliberately NOT the discriminator for the silent
    allow: ``False`` here means "present was not established", which covers both proven
    absence and an unreadable authority.  Anything deciding whether a turn may end in
    silence asks ``project_run_state()`` and requires ``RUN_STATE_PROVEN_ABSENT``.
    """
    return project_run_state(artifact_base) == RUN_STATE_PRESENT


def stop_hook_run_id(
    payload: dict[str, Any],
    *,
    explicit: str = "",
    env: Any = None,
    artifact_base: Any = ".",
) -> str:
    """The Run this session's turn belongs to, or ``""`` when it is bound to none.

    In order: ``--run-id`` on the registration, ``ORCA_QUIESCENCE_RUN_ID`` in the hook's
    environment, then the durable session binding this Run's Coordinator published.  The
    run id is never read out of the payload itself -- the runtime has no idea which Orca
    Run a session drives and would only be relaying whatever the session put there --
    but the payload's ``session_id`` is exactly what the binding is keyed by.
    """
    if explicit:
        return explicit
    from_env = ((env if env is not None else os.environ).get(STOP_HOOK_RUN_ENV) or "").strip()
    if from_env:
        return from_env
    return session_bound_run_id(
        str(payload.get("session_id") or ""), artifact_base=artifact_base
    )


def _unbound_state_dirs(artifact_base: Any) -> tuple[Path, ...]:
    """Where an unattributable session's counter may live, deepest-preferred.

    FINAL attempt-3 R1.  The counter used to live only at the ``artifacts/runs`` root,
    which is exactly the directory the new unreadable-authority refusal cannot write to.
    A refusal whose budget cannot be recorded never advances and therefore never
    releases, which is the one thing this boundary must never do to a live session -- so
    the counter falls back outward to a directory that IS writable.  The runs root stays
    first, so nothing about the ordinary unbound case moves.
    """
    base = Path(artifact_base)
    return (_runs_root(base), base / "artifacts", base, Path(tempfile.gettempdir()))


def _unbound_state_dir(artifact_base: Any) -> Path:
    """The first of the above this process can actually write a counter into."""
    candidates = _unbound_state_dirs(artifact_base)
    for candidate in candidates:
        try:
            if candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK):
                return candidate
        except OSError:
            continue
    return candidates[-1]


def _stop_hook_state_path(run_id: str, artifact_base: Path) -> Path:
    """Where this session's consecutive-block counter lives.

    Inside the run's own directory when there is a run.  When there is not -- an
    unattributable session is now blocked rather than waved through, so its budget has to
    be counted somewhere, and it cannot be counted under a run id nobody could resolve --
    at the ``artifacts/runs`` root, or, when that root is the thing that cannot be read,
    at the first enclosing directory that will take the file.
    """
    root = _runs_root(artifact_base)
    if not run_id:
        return _unbound_state_dir(artifact_base) / STOP_HOOK_UNBOUND_STATE_FILENAME
    return root / run_id / STOP_HOOK_STATE_FILENAME


def clear_unresolved_entry_block_count(artifact_base: Any = ".") -> None:
    """Forget the registration-level counter, because the registration just resolved.

    The shell fallback counts its own refusals; this module is proof the entry point is
    reachable again, so the next unresolvable episode starts with a whole budget instead
    of inheriting a spent one from an episode that has been repaired.  Every directory
    that fallback is allowed to count in is cleared, not just the runs root: the episode
    being repaired here may well have been one where the runs root was unreadable.
    """
    for directory in _unbound_state_dirs(artifact_base):
        try:
            (directory / STOP_HOOK_UNRESOLVED_STATE_FILENAME).unlink()
        except OSError:
            continue


def stop_hook_block_count(run_id: str, session_id: str, *, artifact_base: Path) -> int:
    """How many times in a row this session has already been blocked here."""
    try:
        document = json.loads(
            _stop_hook_state_path(run_id, Path(artifact_base)).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return 0
    if not isinstance(document, dict):
        return 0
    count = document.get(session_id or "")
    return count if isinstance(count, int) and count >= 0 else 0


def set_stop_hook_block_count(
    run_id: str, session_id: str, count: int, *, artifact_base: Path
) -> bool:
    """Record the counter and REPORT WHETHER THE RECORD ACTUALLY TOOK.

    FINAL adversarial review R1.  This used to return ``None`` on every path and swallow
    any write error, on the reasoning that an unwritten counter can only relax a later
    decision.  That reasoning was wrong in the one direction that matters: an increment
    that is not persisted leaves the NEXT invocation reading zero, so the cap is never
    reached and the refusal never ends.  A silent write failure therefore converted the
    promised finite cap into an unbounded block -- the exact live-session wedge the cap
    exists to prevent.

    So the outcome is now a value the caller has to honour, and it is established by
    reading the counter back rather than by trusting the write.  ``_unbound_state_dir()``
    picks its directory with an ``os.access`` preflight, which is a TOCTOU check and no
    proof of anything: permissions can change between the check and the write, the
    counter file itself can already be non-writable, every candidate including the
    temp directory can be unavailable, and a write that raises nothing can still fail for
    quota, read-only or I/O reasons.  Only the read-back settles it.

    Clearing (``count == 0``) reports ``True`` when the counter reads zero for any reason,
    including a file that does not exist -- a clear that cannot be written leaves at most
    a stale count, which spends the budget EARLIER and so cannot wedge anything.
    """
    base = Path(artifact_base)
    path = _stop_hook_state_path(run_id, base)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            document = {}
    except (OSError, ValueError):
        document = {}
    document[session_id or ""] = count
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    except OSError:
        return count == 0 and stop_hook_block_count(run_id, session_id, artifact_base=base) == 0
    return stop_hook_block_count(run_id, session_id, artifact_base=base) == count


#: What the boundary says when it wanted to refuse a turn and could not write down the
#: refusal's own budget.  See "THE TWO DIRECTIONS OF THE FAIL-SAFE" in the module
#: docstring: this releases on purpose, and names the path, so the situation is
#: observable instead of silently ungated.
STOP_HOOK_UNCOUNTABLE_BUDGET_RELEASE_MESSAGE = (
    "orca turn-end boundary: this turn end was NOT gated. The boundary wanted to refuse "
    "it, but it could not record the refusal's own budget at {path}, and a refusal whose "
    "budget cannot be recorded is not bounded: every following turn end would read a "
    "count of zero and be refused again, with nothing left to release the session. An "
    "unbounded block is worse than one unobserved turn end, so this turn is released "
    "instead of refused. Make that path writable -- or point $TMPDIR at a writable "
    "directory -- to restore gating. The refusal that was withheld said: {reason}"
)


def entry_point_hint() -> str:
    """How to re-run this boundary by hand, spelled as a path that actually resolves.

    BUGFIX-I4-R1-REAL-PATH. The hook told the model to re-derive with
    ``python3 tools/run_workflow.py turn-end``, which opens from no working directory --
    the same unresolvable relative path the registration carried. This reports the entry
    point THIS process was started from, so the advice can be pasted.
    """
    argv0 = sys.argv[0] if sys.argv else ""
    return argv0 if argv0.endswith(".py") else "run_workflow.py"


def stop_hook_reason(run_id: str, verdict: dict[str, Any], observation: dict[str, Any]) -> str:
    """What the model is told when the turn is blocked.

    It has to be actionable, because the block only ends when the run's state changes:
    the reason names the rule, the derived state that refused, and the work that is
    outstanding.
    """
    parts = [
        f"Coordinator turn-end boundary REFUSED this turn for run {run_id}: "
        f"{verdict['reason_code']} -- {verdict['detail']}"
    ]
    if observation.get("runnable_actions"):
        parts.append("runnable work: " + ", ".join(observation["runnable_actions"]))
    if observation.get("outstanding_deliveries"):
        parts.append(
            "unacknowledged deliveries: " + ", ".join(observation["outstanding_deliveries"])
        )
    parts.append(
        "Do not end the turn on a progress report. Carry the run to an active dispatch "
        "wait, a durable WAITING_FOR_INPUT, BLOCKED, ESCALATED or a terminal status, "
        f"then finish. Re-derive with: python3 {entry_point_hint()} turn-end --run-id "
        f"{run_id}."
    )
    return "  ".join(parts)


#: The roots the registered command searches for the Skill's ``tools/`` directory, in
#: order.  These are the layouts this registration SUPPORTS:
#:
#: * ``$ORCA_QUIESCENCE_HOME`` -- an operator with the Skill anywhere else;
#: * ``$CLAUDE_PROJECT_DIR/orca-worker-reviewer-orchestration`` -- this repository
#:   checked out as the project (the layout the review probed and found broken);
#: * ``$CLAUDE_PROJECT_DIR/.claude/skills/orca-worker-reviewer-orchestration`` -- the
#:   Skill installed into the project;
#: * ``$HOME/.claude/skills/orca-worker-reviewer-orchestration`` -- the Skill installed
#:   for the user, which is where ``orca skills get`` puts it.
#:
#: NOT supported, deliberately: a bare relative ``tools/run_workflow.py``.  Claude Code
#: runs a hook with the PROJECT directory as its working directory and exports it as
#: ``CLAUDE_PROJECT_DIR``; an installed Skill does not make its own ``tools/`` the hook's
#: cwd, which is exactly why the previous registration could not execute anything.
STOP_HOOK_ENTRY_ROOTS = (
    "$ORCA_QUIESCENCE_HOME",
    "${CLAUDE_PROJECT_DIR:-$PWD}/orca-worker-reviewer-orchestration",
    "${CLAUDE_PROJECT_DIR:-$PWD}/.claude/skills/orca-worker-reviewer-orchestration",
    "$HOME/.claude/skills/orca-worker-reviewer-orchestration",
)

#: What the registered command says when it can resolve no entry point IN A PROJECT THAT
#: HOLDS NO RUN STATE.  That is the one case where a missing tool is evidence of nothing
#: this boundary is about, so the turn is allowed -- out loud, because a registration
#: that silently enforces nothing is indistinguishable from one that works, and that is
#: precisely how an earlier round shipped.
STOP_HOOK_UNRESOLVED_MESSAGE = (
    "orca turn-end boundary: run_workflow.py was not found under "
    "$ORCA_QUIESCENCE_HOME, $CLAUDE_PROJECT_DIR or ~/.claude/skills, so this Stop hook "
    "is enforcing nothing. This project was read and holds no Orca run state, so the "
    "turn was allowed. Fix the registration or remove it."
)

#: The same situation IN A PROJECT THAT DOES HOLD RUN STATE, which is a different fact:
#: a registered boundary that cannot execute has not established that anything is at
#: rest, and this project is one where something might not be.  The turn is refused.
#: Written as a shell-safe, JSON-safe single line because the only thing that can emit it
#: is the ``printf`` at the end of the registration command.
STOP_HOOK_UNRESOLVED_BLOCK_REASON = (
    "Coordinator turn-end boundary: this Stop hook is registered, but run_workflow.py "
    "was not found under $ORCA_QUIESCENCE_HOME, $CLAUDE_PROJECT_DIR or "
    "~/.claude/skills, so the turn end could NOT be checked. This project holds Orca "
    "run state, so the turn is refused rather than passed: a boundary that cannot run "
    "has not established that the run is at rest. Repair the registration to point at "
    "the Skill root that contains tools/run_workflow.py, or remove the Stop hook. "
    "This refusal releases itself after a few consecutive turn ends so it cannot wedge "
    "the session."
)

#: And the THIRD answer, which the previous tail could not give at all.  FINAL
#: attempt-3 R1: a glob cannot establish a child directory under a runs root it may not
#: read, so an unreadable authority left ``H=0`` and took the allow branch -- the same
#: conflation the Python predicate made.  The tail now separates "read it, nothing
#: there" from "could not read it", and the second refuses on the same counter and the
#: same cap as the first.
STOP_HOOK_UNREADABLE_BLOCK_REASON = (
    "Coordinator turn-end boundary: this Stop hook is registered, but run_workflow.py "
    "was not found under $ORCA_QUIESCENCE_HOME, $CLAUDE_PROJECT_DIR or "
    "~/.claude/skills, AND the Orca run state of this project, under artifacts/runs, "
    "could not be read, so it is not known whether there is a run to check. An unreadable "
    "authority is not evidence that this session is unrelated, so the turn is refused "
    "rather than passed. Repair the registration to point at the Skill root that "
    "contains tools/run_workflow.py, and make artifacts/runs readable, or remove the "
    "Stop hook. This refusal releases itself after a few consecutive turn ends so it "
    "cannot wedge the session."
)

#: And what it says once that budget is spent.  A boundary that could refuse forever
#: could wedge a live session, which is worse than a boundary that says it gave up.
STOP_HOOK_UNRESOLVED_RELEASE_MESSAGE = (
    "orca turn-end boundary: run_workflow.py is still not found under "
    "$ORCA_QUIESCENCE_HOME, $CLAUDE_PROJECT_DIR or ~/.claude/skills. The hook has "
    "already refused its budget of consecutive turn ends and is releasing this one "
    "rather than wedging the session. Nothing is being enforced until the registration "
    "is repaired."
)

#: And what it says when it cannot even WRITE that budget down.  FINAL adversarial review
#: R1: the tail used to `printf` the counter, ignore the result and emit `decision: block`
#: regardless, so a counter file it could not write left the next process reading zero and
#: refusing again -- an unbounded block from the very mechanism that promises a bound.
#: The module and the tail now agree on the second of the two directions documented at the
#: top of this file: an unreadable Run authority BLOCKS, an unpersistable block budget
#: RELEASES and says so.  No apostrophes or backslashes: this string is printed from
#: inside a single-quoted shell literal.
STOP_HOOK_UNCOUNTABLE_RELEASE_MESSAGE = (
    "orca turn-end boundary: this turn end was NOT gated. run_workflow.py was not found "
    "under $ORCA_QUIESCENCE_HOME, $CLAUDE_PROJECT_DIR or ~/.claude/skills, and the hook "
    "could not record the refusal budget that keeps its refusals finite -- no candidate "
    "directory (artifacts/runs, artifacts, the project root, $TMPDIR) would take the "
    "counter. A refusal that cannot be counted cannot release itself, and an unbounded "
    "block is worse than one unobserved turn end, so this turn is released instead of "
    "refused. Repair the registration, and make one of those directories writable."
)

def _shell_json_literal(document: str) -> str:
    """A ``printf`` of one JSON object, safe inside the registration's single quotes.

    The registration is a shell command, so every string it can emit has to survive both
    layers: no single quote (it would close the shell literal) and no double quote or
    backslash (it would break the JSON the runtime parses).  Asserted rather than
    escaped, because the strings are ours and a silent escape would hide a typo until a
    live Stop hook produced unparseable output.
    """
    assert "'" not in document and "\\" not in document, document
    return "printf '" + document + "\\n'"


#: THE documented registration command, in one place, so the Skill's copy-paste block,
#: the validator and the tests cannot drift from each other or from what actually runs.
#: Claude Code executes a hook ``command`` through ``sh -c``, so this is a shell command:
#: it resolves the entry point from the roots above and hands the boundary the project
#: directory as its artifact base, which is where ``artifacts/runs/<run-id>/`` lives.
#:
#: What it does when NO root resolves is the part this round changed.  It used to print a
#: notice and let the turn end, which made a broken registration indistinguishable from a
#: working one for the only party that could act on it -- the model.  Now the tail of the
#: command decides the same way the module does: silence when the project holds no run
#: directories, and ``decision: block`` when it holds some, bounded by its own counter at
#: ``artifacts/runs/.stop_hook_unresolved_blocks`` so an unrepairable registration cannot
#: wedge the session.  Plain POSIX ``sh``: a glob rather than ``find`` for the run-state
#: test, and a digits-only guard before the arithmetic, because a shell that errors here
#: would emit no decision at all.
STOP_HOOK_COMMAND = (
    "for r in "
    + " ".join(f'"{root}"' for root in STOP_HOOK_ENTRY_ROOTS)
    + "; do "
    '[ -n "$r" ] && [ -f "$r/tools/run_workflow.py" ] && '
    'exec python3 "$r/tools/run_workflow.py" turn-end-hook '
    '--artifact-base "${CLAUDE_PROJECT_DIR:-$PWD}"; '
    "done; "
    # The tri-state, in `sh`.  H=0 proven absent, H=1 present, H=2 unreadable -- and H
    # STARTS at 2, so every path that fails to establish either of the first two answers
    # is the unreadable one rather than the allow one.  Each step down the chain is
    # positively established before the next is trusted: `[ ! -e "$A" ]` only means "no
    # artifacts directory" once "$P" has been shown to be a listable directory, because
    # `-e` is equally false for a path whose parent may not be searched.
    'P="${CLAUDE_PROJECT_DIR:-$PWD}"; A="$P/artifacts"; R="$A/runs"; H=2; '
    'if [ -d "$P" ] && [ -r "$P" ] && [ -x "$P" ]; then '
    'if [ ! -e "$A" ]; then H=0; '
    'elif [ -d "$A" ] && [ -r "$A" ] && [ -x "$A" ]; then '
    'if [ ! -e "$R" ]; then H=0; '
    'elif [ -d "$R" ] && [ -r "$R" ] && [ -x "$R" ]; then H=0; '
    'for d in "$R"/*/; do [ -d "$d" ] && H=1 && break; done; '
    "fi; fi; fi; "
    'if [ "$H" = 0 ]; then '
    + _shell_json_literal('{"systemMessage":"' + STOP_HOOK_UNRESOLVED_MESSAGE + '"}')
    + "; else "
    # The counter cannot live under a runs root this branch may be refusing BECAUSE it
    # cannot be written; it falls outward to the first directory that will take it, in
    # the same order the module's own fallback uses, so a refusal always advances and
    # therefore always releases.  The runs root stays the first choice, so nothing about
    # the ordinary present-run case moves.
    'D="$P"; [ -d "$A" ] && [ -w "$A" ] && [ -x "$A" ] && D="$A"; '
    '[ -d "$R" ] && [ -w "$R" ] && [ -x "$R" ] && D="$R"; '
    '{ [ -d "$D" ] && [ -w "$D" ]; } || D="${TMPDIR:-/tmp}"; '
    f'C="$D/{STOP_HOOK_UNRESOLVED_STATE_FILENAME}"; '
    'N=$(cat "$C" 2>/dev/null); case "$N" in ""|*[!0-9]*) N=0;; esac; N=$((N+1)); '
    # And the refusal is CONDITIONAL on that counter actually landing.  `printf` can
    # fail after the `-w` tests above pass (the tests are TOCTOU, the file itself may be
    # unwritable, the filesystem may be full or read-only), so its status is checked AND
    # the value is read back; only then is a block emitted.  When the budget cannot be
    # recorded the tail releases with its own message instead of refusing without a
    # bound -- the module does exactly the same thing, for the same reason.
    f'if [ "$N" -le {STOP_HOOK_BLOCK_CAP_DEFAULT} ]; then '
    'if { printf %s "$N" >"$C"; } 2>/dev/null && '
    '[ "$(cat "$C" 2>/dev/null)" = "$N" ]; then '
    'if [ "$H" = 1 ]; then '
    + _shell_json_literal(
        '{"decision":"block","reason":"' + STOP_HOOK_UNRESOLVED_BLOCK_REASON + '"}'
    )
    + "; else "
    + _shell_json_literal(
        '{"decision":"block","reason":"' + STOP_HOOK_UNREADABLE_BLOCK_REASON + '"}'
    )
    # No `rm` here on purpose: this branch is reached BECAUSE the counter could not be
    # written, and deleting a file we failed to write is a side effect with no purpose --
    # worse, where the file is unwritable but its directory is not, the delete would hand
    # the next process a fresh writable counter and let the unbounded refusal resume.
    + "; fi; else "
    + _shell_json_literal(
        '{"systemMessage":"' + STOP_HOOK_UNCOUNTABLE_RELEASE_MESSAGE + '"}'
    )
    + "; fi; else rm -f \"$C\"; "
    + _shell_json_literal(
        '{"systemMessage":"' + STOP_HOOK_UNRESOLVED_RELEASE_MESSAGE + '"}'
    )
    + "; fi; fi"
)

#: What the hook tells a model whose session it cannot attribute to any Run, in a
#: project that holds Run state.  This BLOCKS.  An earlier round only announced it, which
#: reproduced the exact OS-44 defect one level up: the Coordinator still had to remember
#: an invocation (``turn-end-bind``) before ending its turn, and forgetting it was again
#: allowed.  An allow does not re-invoke the model, so an announcement cannot repair a
#: missing binding; a block can, and does.
UNBOUND_SESSION_BLOCK_REASON = (
    "Coordinator turn-end boundary: this Claude Code session is bound to no Orca Run, "
    "so this turn end could not be checked against any run state -- and this project "
    "DOES hold Orca run state, so the turn is refused rather than passed. If you are "
    "driving a Run, bind it once with `python3 {entry} turn-end-bind --run-id RUN_ID` "
    "(or export {run_env}=RUN_ID) and every later turn end of this session is gated on "
    "that run; release it with `--release` when you are done with it. If this session "
    "drives no Run, there is nothing to do: this refusal releases itself after {cap} "
    "consecutive turn ends."
)

#: The same refusal for the third answer: the project's Run authority could not be read
#: at all.  FINAL attempt-3 R1 -- an unreadable ``artifacts/runs`` used to be reported as
#: proof that no runs exist, which is the one answer that licenses a silent allow.  It is
#: not proof of anything, so it refuses, on the same counter and the same cap.
UNREADABLE_RUN_STATE_BLOCK_REASON = (
    "Coordinator turn-end boundary: this Claude Code session is bound to no Orca Run, "
    "and this project's Run state under `artifacts/runs` could not be read, so it is "
    "not known whether there is a run this turn should have been checked against. An "
    "unreadable authority is not evidence that this session is unrelated, so the turn "
    "is refused rather than passed. Make `artifacts/runs` readable, and -- if you are "
    "driving a Run -- bind it once with `python3 {entry} turn-end-bind --run-id RUN_ID` "
    "(or export {run_env}=RUN_ID). This refusal releases itself after {cap} consecutive "
    "turn ends."
)

#: And what it says once that budget is spent.  The unrelated session that got caught by
#: the project's run state is released here, which is why gating on run state rather than
#: on session identity is safe: being wrong costs a bounded number of extra turns.
UNBOUND_SESSION_RELEASE_MESSAGE = (
    "orca turn-end boundary: this session is still bound to no Orca Run. The hook has "
    "refused {blocked} consecutive turn ends of it and is releasing this one rather "
    "than wedging the session, so this turn end was NOT gated. Bind the run with "
    "`python3 {entry} turn-end-bind --run-id RUN_ID` if this session is driving one."
)


def unbound_session_decision(
    payload: dict[str, Any],
    *,
    artifact_base: Any = ".",
    cap: Any = None,
    env: Any = None,
) -> dict[str, Any]:
    """The hook's answer for a session it cannot attribute to a Run.

    THREE cases, and the evidence that separates them is ``project_run_state()``:

    * **Proven no run artifacts in this project** -> silence, and nothing observed.  This
      is the only shape of unattributable session with positive evidence that it is
      unrelated to this boundary, and it is what keeps registering the hook safe for a
      machine that runs other Claude Code sessions.  A hook that chatters at -- or worse,
      gates -- every unrelated session gets uninstalled, and an uninstalled hook enforces
      nothing.
    * **Run artifacts present** -> BLOCK, bounded by the same consecutive-block budget
      the run-bound path uses.  "Unbound in a project that has runs" is either a
      Coordinator that forgot to bind its Run or a registration that is not doing what
      its operator believes, and neither is evidence that this turn may end.  The block
      is the only response that reaches the party who can fix it, because a Stop-hook
      allow ends the turn without re-invoking the model.
    * **The Run authority could not be read** -> BLOCK, on the same budget.  FINAL
      attempt-3 R1: this used to be indistinguishable from the first case, because the
      predicate answered ``False`` for both.  "I could not look" is not "there is nothing
      there", and treating it as the latter reopened the silent turn gap for any project
      whose ``artifacts/runs`` this process cannot list.

    The budget is counted per session in ``.stop_hook_unbound_blocks.json`` at the
    ``artifacts/runs`` root -- or, when that root is the thing that cannot be read, at the
    first enclosing directory that will take the file, so the refusal still advances and
    therefore still releases -- and, as on the bound path, only while ``stop_hook_active``
    says this stop chain is one we started.  And when NO location will take the file, the
    refusal is withheld: FINAL adversarial review R1, the second of the two directions in
    the module docstring.  Unreadable Run authority blocks; an unpersistable block budget
    releases, because a refusal that cannot be counted cannot be escaped.
    """
    base = Path(artifact_base)
    run_state = project_run_state(base)
    if run_state == RUN_STATE_PROVEN_ABSENT:
        return {"suppressOutput": True}
    session_id = str(payload.get("session_id") or "")
    budget = stop_hook_block_cap(env) if cap is None else int(cap)
    blocked_so_far = (
        stop_hook_block_count("", session_id, artifact_base=base)
        if payload.get("stop_hook_active")
        else 0
    )
    if budget > 0 and blocked_so_far >= budget:
        set_stop_hook_block_count("", session_id, 0, artifact_base=base)
        return {
            "systemMessage": UNBOUND_SESSION_RELEASE_MESSAGE.format(
                blocked=blocked_so_far, entry=entry_point_hint()
            )
        }
    if not set_stop_hook_block_count("", session_id, blocked_so_far + 1, artifact_base=base):
        return {
            "systemMessage": STOP_HOOK_UNCOUNTABLE_BUDGET_RELEASE_MESSAGE.format(
                path=_stop_hook_state_path("", base),
                reason=(
                    "this session is bound to no Orca Run and the project's run state "
                    "is "
                    + (
                        "unreadable"
                        if run_state == RUN_STATE_UNREADABLE
                        else "present"
                    )
                    + "."
                ),
            )
        }
    template = (
        UNREADABLE_RUN_STATE_BLOCK_REASON
        if run_state == RUN_STATE_UNREADABLE
        else UNBOUND_SESSION_BLOCK_REASON
    )
    return {
        "decision": "block",
        "reason": template.format(
            entry=entry_point_hint(), run_env=STOP_HOOK_RUN_ENV, cap=budget
        ),
    }


def merge_stop_hook_registration(
    settings: Any, command: str, *, timeout: int = 60
) -> dict[str, Any]:
    """Add this boundary's Stop hook to a settings document without disturbing it.

    Pure, and it composes rather than replaces: Orca's own Stop hook -- which the live
    global settings already carry -- and every other event are left exactly as they were,
    and re-registering the same command is idempotent.  It exists so the documented
    opt-in registration is a tested transformation instead of hand-edited JSON, and it is
    never applied to a file by anything in this repository.
    """
    document = dict(settings) if isinstance(settings, dict) else {}
    hooks = dict(document.get("hooks") or {}) if isinstance(document.get("hooks"), dict) else {}
    stop = list(hooks.get("Stop") or []) if isinstance(hooks.get("Stop"), list) else []
    for entry in stop:
        for hook in (entry.get("hooks") or []) if isinstance(entry, dict) else []:
            if isinstance(hook, dict) and hook.get("command") == command:
                return document
    stop.append({"hooks": [{"type": "command", "command": command, "timeout": timeout}]})
    hooks["Stop"] = stop
    document["hooks"] = hooks
    return document


def _stop_hook_output(decision: dict[str, Any], *, stream: Any) -> None:
    print(json.dumps(decision, sort_keys=True, ensure_ascii=False), file=stream)


def run_stop_hook_cli(
    args: Any,
    *,
    stdin: Any = None,
    stdout: Any = None,
    runner: Callable[[Sequence[str]], tuple[int, str]] | None = None,
    env: Any = None,
) -> int:
    """``run_workflow.py turn-end-hook``: the boundary, as a Claude Code ``Stop`` hook.

    Reads the hook payload on stdin, writes the hook decision as JSON on stdout, and
    always exits 0 -- the verdict is the JSON, and a non-zero exit would be read as the
    hook having failed rather than as a refusal.

    The decision, in the order the guards apply:

    1. **Not a Stop event** -> allow, silently.  A ``SubagentStop`` is a Worker
       finishing, not the Coordinator's turn.
    2. **No run binding** -> it depends on whether there is anything here to be about,
       and that is the ONLY question this boundary is allowed to answer with silence:

       * the project is PROVEN to hold no ``artifacts/runs`` directories -> allow,
         silently.  A session in an unrelated project is positively unrelated, and gating
         every one of them is how this hook gets uninstalled.
       * the project's Run state cannot be read at all -> **block**, on the same budget.
         An unreadable authority is not proof of absence, and the previous build's
         predicate could not tell the two apart.
       * the project holds run state -> **block**, bounded by the budget below.  An
         unattributable session in a project that has runs is either a Coordinator that
         forgot ``turn-end-bind`` or a registration that is not doing what its operator
         thinks, and both of those are the OS-44 defect, not an exemption from it.
         Announcing this instead of blocking is what an earlier round shipped, and it
         cannot work: an allow ends the turn without re-invoking the model, so nobody who
         could repair the binding ever hears about it.
    3. **Consecutive-block budget spent** -> allow, and record the refusal it is letting
       through as a ``quiescence_violation`` with the released source.  This is the
       deliberate escape hatch: ``stop_hook_active`` says the model is already here
       because we blocked it, and a boundary that can block forever is a boundary that
       can wedge a session.  The runtime has its own cap (8 by default); this one is
       lower so the release is ours, tested, and recorded.
    4. **Refused, or authoritative state unreadable** -> block, with the reason the model
       needs to act on.  Unreadable state fails closed here exactly as it does at exit 3
       on the CLI, and it is bounded by the same budget.
    5. **Quiescent** -> allow, and reset the budget.

    Its own defects fail closed too, on the same budget: once a run has been resolved, an
    unexpected exception REFUSES the turn rather than passing it, because a gate that
    could not run has established nothing about the run.  The budget is what stops that
    from wedging a session, and the release is recorded like every other release.
    """
    out = stdout if stdout is not None else sys.stdout
    environ = env if env is not None else os.environ
    payload = read_stop_hook_payload(stdin if stdin is not None else sys.stdin)
    if str(payload.get("hook_event_name") or "Stop") != "Stop":
        _stop_hook_output({"suppressOutput": True}, stream=out)
        return EXIT_STOP_HOOK
    base = Path(getattr(args, "artifact_base", ".") or ".")
    # This module running at all is the proof the registration resolves, so the shell
    # fallback's own refusal budget starts whole again the next time it does not.
    clear_unresolved_entry_block_count(base)
    session_id = str(payload.get("session_id") or "")
    cap = (
        getattr(args, "block_cap", None)
        if getattr(args, "block_cap", None) is not None
        else stop_hook_block_cap(environ)
    )
    run_id = stop_hook_run_id(
        payload,
        explicit=getattr(args, "run_id", "") or "",
        env=environ,
        artifact_base=base,
    )
    if not run_id:
        _stop_hook_output(
            unbound_session_decision(payload, artifact_base=base, cap=cap, env=environ),
            stream=out,
        )
        return EXIT_STOP_HOOK

    # The session resolved, so whatever it was charged while it was unattributable is
    # settled; a later unbound turn starts from a whole budget rather than a spent one.
    # Only when there is something to clear: a gated turn should not leave a counter file
    # behind for a state the session was never in.
    if _stop_hook_state_path("", base).exists():
        set_stop_hook_block_count("", session_id, 0, artifact_base=base)
    # A fresh stop chain: the model is not here because we blocked it, so the budget is
    # whole again.  Reading it this way keeps the counter honest across turns without
    # needing the runtime to hand us a count it does not expose.
    blocked_so_far = (
        stop_hook_block_count(run_id, session_id, artifact_base=base)
        if payload.get("stop_hook_active")
        else 0
    )

    try:
        try:
            verdict, observation = enforce(
                run_id,
                artifact_base=base,
                declared_status=getattr(args, "declare", "") or "",
                runner=runner,
                source=STOP_HOOK_SOURCE,
            )
        except TurnBoundaryUnavailable as error:
            verdict = {
                "quiescent": False,
                "state": "",
                "reason_code": "TURN_BOUNDARY_UNAVAILABLE",
                "detail": str(error),
            }
            observation = {"runnable_actions": [], "outstanding_deliveries": []}
        if verdict["quiescent"]:
            set_stop_hook_block_count(run_id, session_id, 0, artifact_base=base)
            _stop_hook_output({"suppressOutput": True}, stream=out)
            return EXIT_STOP_HOOK
        if cap > 0 and blocked_so_far >= cap:
            set_stop_hook_block_count(run_id, session_id, 0, artifact_base=base)
            _record_stop_hook_release(run_id, verdict, observation, artifact_base=base)
            _stop_hook_output(
                {
                    "systemMessage": (
                        f"turn-end boundary: run {run_id} is still not at rest "
                        f"({verdict['reason_code']}), but the hook has already blocked "
                        f"{blocked_so_far} consecutive turn ends and is releasing this "
                        "one rather than wedging the session. The refusal is recorded in "
                        "the run's coordinator audit."
                    )
                },
                stream=out,
            )
            return EXIT_STOP_HOOK
        reason = stop_hook_reason(run_id, verdict, observation)
        if not set_stop_hook_block_count(
            run_id, session_id, blocked_so_far + 1, artifact_base=base
        ):
            # FINAL adversarial review R1.  The refusal below is only safe because the
            # cap ends it, and the cap only exists while the count can be written down.
            # It could not be, so the refusal is withheld and the release is recorded
            # like any other release the cap grants.
            _record_stop_hook_release(run_id, verdict, observation, artifact_base=base)
            _stop_hook_output(
                {
                    "systemMessage": STOP_HOOK_UNCOUNTABLE_BUDGET_RELEASE_MESSAGE.format(
                        path=_stop_hook_state_path(run_id, base), reason=reason
                    )
                },
                stream=out,
            )
            return EXIT_STOP_HOOK
        _stop_hook_output({"decision": "block", "reason": reason}, stream=out)
        return EXIT_STOP_HOOK
    except Exception as error:  # noqa: BLE001 - the hook's own failure is not an allow
        # FAIL CLOSED on our own defect too, bounded by the same budget.  A gate that
        # crashed has not established that the run is at rest, and this run is one we
        # already resolved -- so "the check could not run" is exactly as much evidence
        # for ending the turn as "the check refused".  The budget is what keeps a
        # persistent internal failure from wedging the session: after it is spent the
        # turn is released and the release is recorded like any other.
        _stop_hook_output(
            _stop_hook_failure_decision(
                run_id,
                session_id,
                error,
                blocked_so_far=blocked_so_far,
                cap=cap,
                artifact_base=base,
            ),
            stream=out,
        )
        return EXIT_STOP_HOOK


def run_bind_cli(args: Any, *, env: Any = None) -> int:
    """``run_workflow.py turn-end-bind``: bind THIS session to a Run, or release it.

    The producer the Stop hook's binding needs.  A prompt-driven Coordinator runs this
    once, right after ``orchestration run-create`` (or after adopting an existing Run),
    and every later turn end of that session is gated on that Run without anything else
    being remembered or exported.

    Exits 0 when the binding was written and 3 -- ``turn-end``'s "no verdict" code --
    when there is no session to bind or the record could not be published.  Loudly,
    rather than as a silent no-op: a Coordinator that believes it is bound and is not is
    the exact failure this verb exists to prevent.
    """
    environ = env if env is not None else os.environ
    session = (getattr(args, "session_id", "") or "").strip() or current_session_id(environ)
    if not session:
        print(
            f"turn-end-bind: no session id (${SESSION_ID_ENV} is unset and --session-id "
            "was not given); nothing was bound. A registered Stop hook will BLOCK this "
            "session's turn ends, up to its consecutive-block cap, while this project "
            "holds Orca run state or its run state cannot be read, and will pass them "
            "over silently only if the project is proven to hold none",
            file=sys.stderr,
        )
        return EXIT_UNAVAILABLE
    base = Path(getattr(args, "artifact_base", ".") or ".")
    release = bool(getattr(args, "release", False))
    path = bind_session_run(
        args.run_id,
        session_id=session,
        artifact_base=base,
        release=release,
    )
    if path is None:
        print(
            f"turn-end-bind: could not write the binding for run {args.run_id} under "
            f"{base}; nothing is bound, so a registered Stop hook will BLOCK this "
            "session's turn ends, up to its consecutive-block cap, while this project "
            "holds Orca run state or its run state cannot be read, and will pass them "
            "over silently only if the project is proven to hold none",
            file=sys.stderr,
        )
        return EXIT_UNAVAILABLE
    summary = {
        "run_id": args.run_id,
        "session_id": session,
        "released": release,
        "binding": str(path),
    }
    if getattr(args, "json", False):
        print(json.dumps(summary, sort_keys=True, ensure_ascii=False))
    else:
        verb = "released" if release else "bound"
        print(f"turn-end-bind: session {session} {verb} run {args.run_id} ({path})")
    return EXIT_QUIESCENT


#: The reason code a hook failure carries into the block and into the audit, so "the
#: gate crashed" is a distinguishable verdict rather than an unexplained refusal.
STOP_HOOK_FAILURE_REASON_CODE = "TURN_BOUNDARY_HOOK_FAILED"


def _stop_hook_failure_decision(
    run_id: str,
    session_id: str,
    error: BaseException,
    *,
    blocked_so_far: int,
    cap: int,
    artifact_base: Path,
) -> dict[str, Any]:
    """The hook's answer when the hook itself failed, for a run it had already resolved.

    Fail closed, bounded.  The turn is refused, because a boundary that crashed has not
    observed the run and "we could not check" is not "there is nothing to check".  Once
    the consecutive-block budget is spent the turn is released and the release is
    published to the run's audit under the same ``..._cap_released`` source as any other,
    so a stalled turn that got through on our own defect is a fact in the artifacts.

    Every step is individually guarded: this runs inside the handler of last resort, and
    an exception raised here would leave the runtime with no decision at all.
    """
    detail = f"the turn-end boundary hook failed before it could reach a verdict: {error}"
    verdict = {
        "quiescent": False,
        "state": "",
        "reason_code": STOP_HOOK_FAILURE_REASON_CODE,
        "detail": detail,
    }
    observation: dict[str, Any] = {"runnable_actions": [], "outstanding_deliveries": []}
    if cap > 0 and blocked_so_far >= cap:
        try:
            set_stop_hook_block_count(run_id, session_id, 0, artifact_base=artifact_base)
            _record_stop_hook_release(run_id, verdict, observation, artifact_base=artifact_base)
        except Exception:  # noqa: BLE001 - the handler of last resort answers regardless
            pass
        return {
            "systemMessage": (
                f"turn-end boundary hook failed for run {run_id} ({error}). It has "
                f"already refused {blocked_so_far} consecutive turn ends on this defect "
                "and is releasing this one rather than wedging the session, so this turn "
                "end was NOT gated. Run "
                f"`python3 {entry_point_hint()} turn-end --run-id {run_id}` by hand."
            )
        }
    try:
        counted = set_stop_hook_block_count(
            run_id, session_id, blocked_so_far + 1, artifact_base=artifact_base
        )
    except Exception:  # noqa: BLE001 - the handler of last resort answers regardless
        counted = False
    if not counted:
        # FINAL adversarial review R1, and the last place it can bite: a hook that has
        # already failed, refusing on a budget it cannot write, would refuse forever.
        try:
            _record_stop_hook_release(run_id, verdict, observation, artifact_base=artifact_base)
        except Exception:  # noqa: BLE001 - the handler of last resort answers regardless
            pass
        return {
            "systemMessage": STOP_HOOK_UNCOUNTABLE_BUDGET_RELEASE_MESSAGE.format(
                path=_stop_hook_state_path(run_id, Path(artifact_base)),
                reason=detail,
            )
        }
    return {
        "decision": "block",
        "reason": (
            f"Coordinator turn-end boundary FAILED for run {run_id}: "
            f"{STOP_HOOK_FAILURE_REASON_CODE} -- {detail}  The turn is refused rather "
            "than passed, because a boundary that could not run has not established that "
            "the run is at rest. Re-derive by hand with: python3 "
            f"{entry_point_hint()} turn-end --run-id {run_id}, and carry the run to an "
            "active dispatch wait, a durable WAITING_FOR_INPUT, BLOCKED, ESCALATED or a "
            f"terminal status before finishing. This refusal releases itself after {cap} "
            "consecutive turn ends."
        ),
    }


def _record_stop_hook_release(
    run_id: str, verdict: dict[str, Any], observation: dict[str, Any], *, artifact_base: Path
) -> None:
    """The one shape in which this boundary lets a forbidden turn end: recorded, always.

    ``enforce`` already published this verdict; this second record is what makes the
    RELEASE itself observable, so "the cap let a stalled turn through" is a fact in the
    audit rather than something only the terminal saw.
    """
    try:
        run_logging.append_coordinator_audit_record(
            run_id,
            run_logging.EVENT_QUIESCENCE_VIOLATION,
            {
                "run_status": observation.get("run_status", ""),
                "status_authority": observation.get("status_authority", ""),
                "next_node": verdict.get("next_node", ""),
                "active_dispatches": len(observation.get("active_dispatches") or []),
                "reason_code": verdict["reason_code"],
                "detail": (
                    "the Stop-hook consecutive-block cap was reached, so the turn was "
                    f"released despite the refusal: {verdict['detail']}"
                ),
                "delivery_id": (observation.get("outstanding_deliveries") or [""])[0],
                "source": STOP_HOOK_SOURCE_RELEASED,
            },
            base=artifact_base,
        )
    except (OSError, run_logging.RunLoggingError):
        return
