"""The audit wrapper that rides a node's checkpoint, with no LangGraph dependency.

Split out of :mod:`.graph` for one reason: ``graph`` imports ``langgraph.graph`` at module
scope, so anything that wanted ``_audited`` had to drag the whole optional runtime in with
it. ``scripts/test_os42_audit.py`` did exactly that and raised ``ModuleNotFoundError:
langgraph`` on every dependency-absent CI job -- an outright error, not a declared skip.

The wrapper never needed LangGraph: it composes a node callable with the pure emitters in
:mod:`.audit`. Living here, the audit-outbox behaviour it implements is testable in BOTH
CI lanes rather than only the one where the graph runtime happens to be installed.
``graph`` re-exports it, so every existing caller and reference is unchanged.
"""
from __future__ import annotations

from typing import Any

from . import audit


def _audited(node: Any, sink: Any, emitter: Any) -> Any:
    """Wrap a node so the audit INTENT rides the checkpoint and delivery is retried.

    The wrapper, not the node body, is where this happens, so the node functions keep
    their signatures and no existing caller or test moves.

    Order matters and each step is deliberate:

    1. the node computes the transition;
    2. the pure emitter turns that transition into outbox entries;
    3. the entries are merged into ``audit_outbox`` -- which is CHECKPOINTED, so an
       undelivered intent survives a crash and is retried by the next node or by a
       resume of this thread;
    4. delivery is attempted and only DELIVERED entries are removed.

    Step 4 cannot raise and cannot alter anything the node decided: ``flush_outbox`` is
    total, and its result is written only back into the outbox itself.  So an audit
    failure leaves a retriable intent and changes no lifecycle decision -- both
    constraints at once.

    Every node is wrapped, not only the three that emit: a node that emits nothing still
    retries whatever an EARLIER node failed to deliver, which is what makes "retried on
    the next node" true rather than aspirational.
    """

    def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        new_state = node(state)
        entries = emitter(state, new_state) if emitter is not None else ()
        outbox = audit.merge_outbox(new_state, entries)
        if not outbox:
            return new_state
        return {**new_state, "audit_outbox": audit.flush_outbox(sink, outbox)}

    return wrapped
