"""Test-only logical trace normalization for the legacy E2E parity oracle."""
from __future__ import annotations
from typing import Any

TRACE_FIELDS = ("sequence", "node", "route", "phase", "phase_iteration",
                "final_review_iteration", "role", "round_kind", "intent_id",
                "event_id", "gate", "terminal_status", "reason_code")


def normalize_trace(entries: list[dict[str, Any]]) -> tuple[tuple[tuple[str, Any], ...], ...]:
    return tuple(tuple((key, entry.get(key)) for key in TRACE_FIELDS) for entry in entries)
