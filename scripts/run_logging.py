#!/usr/bin/env python3
"""Run-scoped ORCHESTRATOR_LOG.md / TIMING_LOG.md writers.

OS-17: SKILL.md section 9's artifact path contract has always named these two
files as something a Run leaves behind under its own `<ARTIFACT_ROOT>`, but
nothing in the actual orchestration execution path ever wrote them -- a real
run produced phase/review artifacts and nothing else. This module is the
"small logging helper" section 6 of OS-17 asks for: it owns formatting and
appending, and reads everything it writes from data the caller already has
(a `RuntimeAttempt`, a lifecycle outcome, a timestamp pair) rather than
deriving anything new. It is deliberately not a telemetry subsystem -- no
aggregation, no analysis, no background writer, just two append-only markdown
tables under the run's own artifact root, sourced from state this repository
already tracks.

Standard library only, like every other module in scripts/.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.task_context import ensure_run_artifact_root
except ModuleNotFoundError:  # direct `python3 scripts/...` execution
    from task_context import ensure_run_artifact_root


ORCHESTRATOR_LOG_FILENAME = "ORCHESTRATOR_LOG.md"
TIMING_LOG_FILENAME = "TIMING_LOG.md"

# The columns are the whole schema. Both tables are append-only and every row
# fills every column (blank string where a field does not apply to that
# event) so a later parser can split on `|` without special-casing rows.
ORCHESTRATOR_LOG_COLUMNS = (
    "timestamp",
    "event",
    "phase",
    "role",
    "iteration",
    "task_id",
    "dispatch_id",
    "terminal",
    "action",
    "reuse",
    "verdict",
    "result",
    "detail",
)
TIMING_LOG_COLUMNS = (
    "timestamp",
    "event",
    "phase",
    "role",
    "iteration",
    "started_at",
    "ended_at",
    "duration_s",
    "detail",
)

# Section 2's four terminal run statuses. A fifth value is not a stricter
# status this module invented -- it is a caller passing something that is not
# one of the four the contract names, and that fails closed rather than being
# written as an unrecognized string a later reader has to guess about.
RUN_STATUS_VALUES = ("COMPLETED", "BLOCKED", "ERROR", "ESCALATED")


class RunLoggingError(ValueError):
    """Raised for a caller mistake (e.g. an unknown run status), never for I/O."""


def now_iso() -> str:
    """The one timestamp format both logs use: UTC, ISO-8601, sortable as text."""
    return datetime.now(timezone.utc).isoformat()


def elapsed_seconds(started_at: str, ended_at: str) -> float | str:
    """`ended_at - started_at` in seconds, or "" when either side can't be parsed.

    Never raises: a malformed or missing timestamp is a blank duration cell,
    not a reason to fail the event it is attached to.
    """
    if not started_at or not ended_at:
        return ""
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
    except ValueError:
        return ""
    return (end - start).total_seconds()


def _escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _ensure_table(path: Path, columns: tuple[str, ...]) -> None:
    """Write the header once. Never called again once the file exists.

    Idempotent the same way ensure_run_artifact_root() is: the header is
    written on first use and every later call is a pure append.
    """
    if path.exists():
        return
    header = "| " + " | ".join(columns) + " |\n"
    divider = "| " + " | ".join("---" for _ in columns) + " |\n"
    path.write_text(header + divider, encoding="utf-8")


def _append_row(path: Path, columns: tuple[str, ...], values: dict[str, Any]) -> None:
    row = "| " + " | ".join(_escape(values.get(column, "")) for column in columns) + " |\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(row)


def orchestrator_log_path(run_id: str, *, base: Path | None = None) -> Path:
    """artifacts/runs/<run_id>/ORCHESTRATOR_LOG.md, provisioning the run root."""
    return ensure_run_artifact_root(run_id, base=base) / ORCHESTRATOR_LOG_FILENAME


def timing_log_path(run_id: str, *, base: Path | None = None) -> Path:
    """artifacts/runs/<run_id>/TIMING_LOG.md, provisioning the run root."""
    return ensure_run_artifact_root(run_id, base=base) / TIMING_LOG_FILENAME


def log_orchestrator_event(
    run_id: str,
    *,
    base: Path | None = None,
    event: str,
    phase: str = "",
    role: str = "",
    iteration: int | str = "",
    task_id: str = "",
    dispatch_id: str = "",
    terminal: str = "",
    action: str = "",
    reuse: str = "",
    verdict: str = "",
    result: str = "",
    detail: str = "",
    timestamp: str | None = None,
) -> Path:
    """Append one row to this run's ORCHESTRATOR_LOG.md. Returns the file path.

    `verdict` is the reviewer-role gate decision (PASS/FAIL) already parsed out of
    the settled dispatch's own body -- distinct from `result`'s `outcome=...`, which
    only says the dispatch/process settled successfully. A Reviewer can settle
    successfully while its review verdict is FAIL (the normal correction-loop case),
    so `result`'s `outcome=succeeded` alone cannot answer "did this phase/iteration
    PASS?" -- that is what this column is for. Blank for non-reviewer dispatches and
    for any event this run's caller did not attach a parsed verdict to.
    """
    path = orchestrator_log_path(run_id, base=base)
    _ensure_table(path, ORCHESTRATOR_LOG_COLUMNS)
    _append_row(
        path,
        ORCHESTRATOR_LOG_COLUMNS,
        {
            "timestamp": timestamp or now_iso(),
            "event": event,
            "phase": phase,
            "role": role,
            "iteration": iteration,
            "task_id": task_id,
            "dispatch_id": dispatch_id,
            "terminal": terminal,
            "action": action,
            "reuse": reuse,
            "verdict": verdict,
            "result": result,
            "detail": detail,
        },
    )
    return path


def log_timing_event(
    run_id: str,
    *,
    base: Path | None = None,
    event: str,
    phase: str = "",
    role: str = "",
    iteration: int | str = "",
    started_at: str = "",
    ended_at: str = "",
    duration_seconds: float | str = "",
    detail: str = "",
    timestamp: str | None = None,
) -> Path:
    """Append one row to this run's TIMING_LOG.md. Returns the file path.

    `duration_seconds` is optional. When the caller supplies both `started_at` and
    `ended_at` but omits (or leaves blank) `duration_seconds`, it is derived here via
    `elapsed_seconds()` -- the same computation the Python harness path used to do at
    every call site. Deriving it once, in the writer both paths share, is what keeps
    a live Coordinator's `timing-event` CLI calls (which only ever had timestamps to
    give it) numerically identical to `OrcaRuntimeHarness`'s own rows, instead of the
    CLI path silently leaving `duration_s` blank while the Python path filled it in.
    An explicit `duration_seconds` (including `0.0`) is never overridden.
    """
    path = timing_log_path(run_id, base=base)
    _ensure_table(path, TIMING_LOG_COLUMNS)
    duration = duration_seconds
    if duration in ("", None) and started_at and ended_at:
        duration = elapsed_seconds(started_at, ended_at)
    if isinstance(duration, float):
        duration = f"{duration:.3f}"
    _append_row(
        path,
        TIMING_LOG_COLUMNS,
        {
            "timestamp": timestamp or now_iso(),
            "event": event,
            "phase": phase,
            "role": role,
            "iteration": iteration,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_s": duration,
            "detail": detail,
        },
    )
    return path


def log_run_status(
    run_id: str,
    status: str,
    *,
    base: Path | None = None,
    reason: str = "",
    run_started_at: str = "",
) -> None:
    """The one run-end event: one ORCHESTRATOR_LOG row, one TIMING_LOG row.

    `status` is fail-closed against RUN_STATUS_VALUES -- an unrecognized value
    is a caller bug, not a status this file should record as if it were valid.
    """
    if status not in RUN_STATUS_VALUES:
        raise RunLoggingError(
            f"unknown run status: {status!r}; expected one of {RUN_STATUS_VALUES}"
        )
    ended_at = now_iso()
    log_orchestrator_event(
        run_id,
        base=base,
        event="run_end",
        result=status,
        detail=reason,
        timestamp=ended_at,
    )
    log_timing_event(
        run_id,
        base=base,
        event="run_end",
        started_at=run_started_at,
        ended_at=ended_at,
        duration_seconds=elapsed_seconds(run_started_at, ended_at),
        detail=status,
        timestamp=ended_at,
    )


# ---- CLI: the same three writers, for a Coordinator driving Orca by hand ----------
# OrcaRuntimeHarness calls the functions above directly from Python. A live
# Coordinator following SKILL.md's own procedure drives Orca through `orca ...`
# Bash commands instead and has no Python object to call into -- this is that
# same logging reused from the other side of that gap, not a second
# implementation. `--base` defaults to the current directory (no override, no
# flag passed), so `python3 scripts/run_logging.py ... --run-id <id>` run from
# the repository root writes to the real artifacts/runs/<run-id>/, exactly
# where SKILL.md section 9 already says every run-scoped artifact lives.


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base", default=None, help="defaults to the current directory")
    parser.add_argument("--phase", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--iteration", default="")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_logging.py",
        description=(
            "Append one row to this run's ORCHESTRATOR_LOG.md or TIMING_LOG.md. "
            "For a live Coordinator driving Orca by hand; OrcaRuntimeHarness calls "
            "the same functions directly and does not need this CLI."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    orchestrator = subparsers.add_parser(
        "orchestrator-event", help="append a row to ORCHESTRATOR_LOG.md"
    )
    _add_common_arguments(orchestrator)
    orchestrator.add_argument("--event", required=True)
    orchestrator.add_argument("--task-id", default="")
    orchestrator.add_argument("--dispatch-id", default="")
    orchestrator.add_argument("--terminal", default="")
    orchestrator.add_argument("--action", default="", choices=("", "created", "reused"))
    orchestrator.add_argument("--reuse", default="")
    orchestrator.add_argument("--verdict", default="")
    orchestrator.add_argument("--result", default="")
    orchestrator.add_argument("--detail", default="")

    timing = subparsers.add_parser("timing-event", help="append a row to TIMING_LOG.md")
    _add_common_arguments(timing)
    timing.add_argument("--event", required=True)
    timing.add_argument("--started-at", default="")
    timing.add_argument("--ended-at", default="")
    timing.add_argument("--duration-seconds", default="")
    timing.add_argument("--detail", default="")

    status = subparsers.add_parser(
        "run-status", help="append the one run-end row to both logs"
    )
    status.add_argument("--run-id", required=True)
    status.add_argument("--base", default=None, help="defaults to the current directory")
    status.add_argument("--status", required=True, choices=RUN_STATUS_VALUES)
    status.add_argument("--reason", default="")
    status.add_argument("--run-started-at", default="")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    base = Path(args.base) if args.base else None
    if args.command == "orchestrator-event":
        path = log_orchestrator_event(
            args.run_id,
            base=base,
            event=args.event,
            phase=args.phase,
            role=args.role,
            iteration=args.iteration,
            task_id=args.task_id,
            dispatch_id=args.dispatch_id,
            terminal=args.terminal,
            action=args.action,
            reuse=args.reuse,
            verdict=args.verdict,
            result=args.result,
            detail=args.detail,
        )
    elif args.command == "timing-event":
        path = log_timing_event(
            args.run_id,
            base=base,
            event=args.event,
            phase=args.phase,
            role=args.role,
            iteration=args.iteration,
            started_at=args.started_at,
            ended_at=args.ended_at,
            duration_seconds=args.duration_seconds,
            detail=args.detail,
        )
    else:
        log_run_status(
            args.run_id,
            args.status,
            base=base,
            reason=args.reason,
            run_started_at=args.run_started_at,
        )
        path = orchestrator_log_path(args.run_id, base=base)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
