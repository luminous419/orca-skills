#!/usr/bin/env python3
"""Installed-Skill entry point for the deterministic graph (adapter supplied by caller)."""
from __future__ import annotations
import importlib.metadata


def dependency_version() -> str:
    try:
        import langgraph
        import langgraph.graph
    except ImportError as exc:
        raise RuntimeError("LANGGRAPH_DEPENDENCY_MISSING: install requirements-langgraph.txt") from exc
    version=importlib.metadata.version("langgraph")
    if version!="0.2.76": raise RuntimeError(f"LANGGRAPH_VERSION_UNSUPPORTED: {version}")
    return version


if __name__=="__main__": print(f"deterministic workflow runtime ready (langgraph {dependency_version()})")
