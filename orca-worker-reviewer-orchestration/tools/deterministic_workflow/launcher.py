"""Executable entry point for the deterministic workflow graph.

This is the runnable half of the engine: it builds state, selects an adapter, invokes or
resumes the compiled graph with an explicit recursion limit, and maps the terminal status
onto a process exit code.  With the fake adapter it runs a complete workflow with no Orca
runtime present.

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
import sys
import tempfile
from pathlib import Path
from typing import Any

from .contracts import BASE_CAPABILITIES
from .executor import terminal_node
from .runtime_state import resolve_runtime_state
from .state import StateError, initial_state, normalize_malformed_state, validate_state

# Terminal status -> process exit code.  Distinct non-zero codes let a caller tell a
# quality/decision block apart from an exhausted iteration budget.
EXIT_CODES = {"COMPLETED": 0, "BLOCKED": 1, "ESCALATED": 2}
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


def default_runtime_state_dir() -> Path:
    override = os.environ.get(RUNTIME_STATE_DIR_ENV)
    return Path(override) if override else Path(tempfile.gettempdir()) / RUNTIME_STATE_DIR_NAME


def default_runtime_state_path(run_id: str, thread_id: str) -> Path:
    """A stable per-run ledger path, so a rerun of the same run recovers its own receipts."""
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in f"{run_id}__{thread_id}")
    return default_runtime_state_dir() / f"{safe}.json"


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
    """Build a validated initial state from a small JSON launch specification."""
    if not isinstance(spec, dict):
        raise LauncherError("state specification must be a JSON object")
    phases = spec.get("phases") or list(CANONICAL_PHASES)
    if not isinstance(phases, list) or not phases:
        raise LauncherError("phases must be a non-empty list")
    capabilities = spec.get("capabilities")
    capabilities = frozenset(capabilities) if capabilities else BASE_CAPABILITIES
    try:
        return dict(initial_state(
            run_id=spec.get("run_id", "run_launcher"),
            thread_id=spec.get("thread_id", "launcher"),
            phases=tuple(phases), capabilities=capabilities,
            risk=spec.get("risk", "high"), max_iterations=spec.get("max_iterations", 5)))
    except (StateError, TypeError, ValueError, KeyError, IndexError) as exc:
        raise LauncherError(f"invalid state specification: {exc}") from exc


def execute_state(raw_state: dict[str, Any], *, adapter: Any, checkpointer: Any = None,
                  runtime_state: Any = None, recursion_limit: int | None = None,
                  thread_id: str | None = None, interrupt_before: list[str] | None = None,
                  interrupt_after: list[str] | None = None) -> dict[str, Any]:
    """Run the compiled graph to a terminal state, failing closed on malformed input.

    This validates the raw mapping before invoking, so the process entry point reports the
    precise ``StateError`` for any malformed input, not just the unknown-field case.  The
    compiled graph boundary (``GuardedWorkflowGraph``) enforces the closed field set
    independently, so a caller that bypasses this function still fails closed.

    A durable ``RuntimeStatePort`` is required, here as everywhere: it is resolved (and
    refused if absent) before the state is even inspected.  ``run_cli`` supplies one by
    default, so the shipped command line is crash-safe without extra flags.
    """
    from .graph import build_graph

    resolve_runtime_state(adapter, runtime_state)
    try:
        validate_state(dict(raw_state), expected_thread_id=raw_state.get("thread_id", ""))
    except (StateError, TypeError, ValueError, KeyError, AttributeError) as exc:
        return terminal_node(normalize_malformed_state(
            raw_state, code="MALFORMED_STATE", message=str(exc)))

    config: dict[str, Any] = {"recursion_limit": recursion_limit or default_recursion_limit(raw_state)}
    if checkpointer is not None:
        config["configurable"] = {"thread_id": thread_id or raw_state["thread_id"]}
    graph = build_graph(adapter, checkpointer=checkpointer, runtime_state=runtime_state,
                        interrupt_before=interrupt_before, interrupt_after=interrupt_after)
    return graph.invoke(raw_state, config)


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
    return {
        "run_id": state.get("run_id"), "workflow_id": state.get("workflow_id"),
        "terminal_status": status, "terminal_reason": state.get("terminal_reason"),
        "requested_phases": state.get("requested_phases"),
        "phase_iterations": state.get("phase_iterations"),
        "final_review_iterations": state.get("final_review_iterations"),
        "trace_length": len(state.get("logical_trace") or []),
        "exit_code": EXIT_CODES.get(status, USAGE_EXIT_CODE),
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
    parser.add_argument("--adapter", choices=("fake",), default="fake",
                        help="adapter to execute with (only the Orca-independent fake ships here)")
    parser.add_argument("--runtime-state",
                        help="JSON file for the durable idempotency ledger "
                             f"(default: ${RUNTIME_STATE_DIR_ENV} or the system temp dir)")
    parser.add_argument("--recursion-limit", type=int,
                        help="override the computed LangGraph recursion limit")
    parser.add_argument("--json", action="store_true", help="print the machine-readable summary")
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        version = require_runtime()
        if args.check_runtime:
            print(f"deterministic workflow runtime ready (langgraph {version})")
            return 0
        state, results = _launch_inputs(args)
        from .runtime_state import FileRuntimeStateStore
        # Durable by default: without an explicit path the run still gets a real on-disk
        # ledger, because an unguarded default is exactly what lets a restart duplicate an
        # external Task/Dispatch.
        ledger_path = Path(args.runtime_state) if args.runtime_state else default_runtime_state_path(
            state["run_id"], state["thread_id"])
        runtime_state = FileRuntimeStateStore(ledger_path)
        from .fake_adapter import FakeAdapter
        adapter = FakeAdapter(results, runtime_state=runtime_state)
        final = execute_state(state, adapter=adapter, runtime_state=runtime_state,
                              recursion_limit=args.recursion_limit)
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


def _launch_inputs(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if args.demo:
        return build_state({"run_id": "run_demo", "thread_id": "demo",
                            "phases": list(CANONICAL_PHASES)}), demo_results()
    if not args.state or not args.results:
        raise LauncherError("--demo, or both --state and --results, are required")
    results = _read_json(args.results, "--results")
    if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
        raise LauncherError("--results must be a JSON list of settlement result objects")
    return build_state(_read_json(args.state, "--state")), results


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
