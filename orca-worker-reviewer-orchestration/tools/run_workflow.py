#!/usr/bin/env python3
"""Installed-Skill CLI entry point for the OS-40 deterministic workflow graph.

Runs the compiled graph to a terminal state and exits with that status's code
(COMPLETED=0, BLOCKED=1, ESCALATED=2; unusable input or runtime=3).  ``--demo`` executes
the canonical 5-phase workflow with the fake adapter and needs no Orca runtime.

The canonical workflow needs roughly 68 graph steps, well past LangGraph's default
``recursion_limit`` of 25, so every entry point sets the limit explicitly.  See
``deterministic_workflow/launcher.py`` for the step accounting.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # direct ``python3 tools/run_workflow.py`` execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from deterministic_workflow.launcher import USAGE_EXIT_CODE, LauncherError, require_runtime, run_cli
except ImportError:  # repository layout, where the engine lives under scripts/
    from scripts.deterministic_workflow.launcher import (  # type: ignore[no-redef]
        USAGE_EXIT_CODE, LauncherError, require_runtime, run_cli)


def dependency_version() -> str:
    """Backwards-compatible probe for the pinned LangGraph runtime."""
    try:
        return require_runtime()
    except LauncherError as exc:
        raise RuntimeError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    try:
        return run_cli(argv)
    except LauncherError as exc:
        print(f"run_workflow: {exc}", file=sys.stderr)
        return USAGE_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
