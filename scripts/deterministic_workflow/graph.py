"""Executable LangGraph StateGraph for OS-40."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from weakref import WeakKeyDictionary

from langgraph.graph import END, START, StateGraph

from .executor import (advance_phase_node, apply_result_node, execute_intent_node,
                       prepare_intent_node, route_node, terminal_node,
                       validate_node, validate_settlement_node)
from .graph_spec import NODES, ROUTE_TARGETS, validate_graph_spec
from .runtime_state import resolve_runtime_state
from .state import (StateError, WorkflowState, normalize_malformed_state, typed_update,
                    validate_state)

CLOSED_STATE_FIELDS = frozenset(WorkflowState.__required_keys__)


def unknown_state_fields(value: Any) -> tuple[str, ...]:
    return tuple(sorted(set(value) - CLOSED_STATE_FIELDS)) if isinstance(value, Mapping) else ()


# Every state-ingress API the façade overrides with its own guarded implementation.
# Derived by inspecting the installed ``CompiledStateGraph`` for public callables that
# accept graph state (a parameter named ``input``, ``inputs`` or ``values``); the test
# ``test_declared_guard_list_matches_the_installed_runtime`` keeps this honest.
GUARDED_INGRESS = frozenset({
    "invoke", "ainvoke", "stream", "astream", "batch", "abatch",
    "update_state", "aupdate_state",
})

# Convenience wrappers around the closed ``state.UPDATE_COMMANDS`` vocabulary.  They are
# not LangGraph ingress APIs -- each one funnels into the guarded ``update_state`` above --
# so they are named separately from :data:`GUARDED_INGRESS`, which must keep matching the
# installed runtime's own surface.
TYPED_UPDATE_API = frozenset({"update_state_command", "aupdate_state_command"})

# ``as_node`` selects which node a resumed run continues from, so it can skip VALIDATE
# entirely.  Only real graph nodes are accepted, and -- because VALIDATE may be skipped --
# every update is validated against the complete merged checkpoint at this boundary
# instead of relying on VALIDATE running afterwards.
ALLOWED_UPDATE_NODES = frozenset(NODES)

# The only names ``__getattr__`` will delegate.  Each reads existing state, topology,
# schema or metadata; none accepts graph state, and none hands back an unguarded runnable.
READ_ONLY_PASSTHROUGH = frozenset({
    "get_state", "aget_state", "get_state_history", "aget_state_history",
    "get_graph", "aget_graph", "get_subgraphs", "aget_subgraphs",
    "get_input_schema", "get_output_schema", "get_input_jsonschema",
    "get_output_jsonschema", "config_schema", "get_config_jsonschema", "get_name",
    "name", "config_specs", "checkpointer",
    "input_channels", "output_channels", "stream_channels", "stream_mode",
})


# The compiled graph is kept off the façade entirely, in a module-private weak map keyed by
# the façade instance.  Renaming an attribute would not have been enough: any attribute --
# ``compiled``, ``_compiled``, or a name-mangled slot -- still shows up on the object and
# still hands the raw graph back to anyone holding it.  Storing it here means the object
# returned by ``build_graph`` has no member, public or private, that yields an unguarded
# graph, which is what ``test_no_public_member_of_the_facade_yields_an_unguarded_graph``
# asserts.  The map is weak so a discarded façade does not pin its graph in memory.
_COMPILED_GRAPHS: "WeakKeyDictionary[Any, Any]" = WeakKeyDictionary()


def _merge_checkpoint(current: dict[str, Any], values: Any) -> dict[str, Any]:
    """The exact checkpoint an update would commit.

    LangGraph omits a channel from a snapshot when its value is ``None``, so the closed
    field set is restored explicitly before the caller's values are merged over it; the
    result is the complete state ``validate_state`` has to accept.  An empty snapshot means
    there is no checkpoint on this thread, and an update to a thread that has never run is
    refused rather than seeded blind.
    """
    if not current:
        raise StateError("MALFORMED_STATE:no checkpoint to update")
    merged: dict[str, Any] = {field: None for field in CLOSED_STATE_FIELDS}
    merged.update(deepcopy(current))
    merged.update(deepcopy(dict(values)))
    return merged


class GuardedWorkflowGraph:
    """Compiled-graph façade that fails closed on input LangGraph would silently drop.

    ``StateGraph`` filters an input mapping down to its declared channels, so a key that is
    not part of the closed ``WorkflowState`` field set never reaches ``VALIDATE`` and the
    state is accepted as if the caller had never sent it.  The closed field set is therefore
    unenforceable *inside* the graph; it has to be checked at the invocation boundary, which
    is what this façade is.

    The façade is **deny-by-default**.  An allow-by-default ``__getattr__`` is what let
    ``batch`` and ``ainvoke`` through when only ``invoke``/``stream``/``update_state`` were
    overridden, and it would do the same for any ingress API a future LangGraph adds.  So
    only :data:`GUARDED_INGRESS` (guarded here) and :data:`READ_ONLY_PASSTHROUGH` (state
    reads, topology, schema, metadata) are reachable; every other name -- including
    composition APIs such as ``bind``, ``pipe``, ``with_config``, ``copy`` and ``builder``,
    which would hand back an unguarded runnable -- raises ``AttributeError``.

    Resume (``invoke(None, config)`` and its async twin) passes straight through: resume
    carries no input mapping to inspect, and the persisted state was validated on entry.

    The compiled graph is not stored on the façade at all -- see ``_COMPILED_GRAPHS``.  It
    used to be reachable as ``.compiled``, a deliberate, documented, test-only handle, but a
    documented intention is not a fail-closed boundary: that one public attribute undid
    every guard above it.  Tests that need to observe raw LangGraph behaviour now compile
    their own graph over the same ``WorkflowState`` schema rather than unwrapping a guarded
    instance.
    """

    def __init__(self, compiled: Any) -> None:
        _COMPILED_GRAPHS[self] = compiled

    def __getattr__(self, name: str) -> Any:
        if name in READ_ONLY_PASSTHROUGH:
            return getattr(_COMPILED_GRAPHS[self], name)
        raise AttributeError(
            f"{type(self).__name__} does not expose {name!r}. The workflow graph is "
            "deny-by-default so that no unguarded state-ingress or graph-unwrapping API is "
            f"reachable; guarded entry points are: {', '.join(sorted(GUARDED_INGRESS))}."
        )

    @staticmethod
    def _rejection(value: Any) -> dict[str, Any] | None:
        """The terminal state for input the graph must refuse, or None to proceed."""
        if value is None:                       # resume: nothing to inspect
            return None
        if not isinstance(value, Mapping):
            return terminal_node(normalize_malformed_state(
                value, code="MALFORMED_STATE",
                message=f"state input must be a mapping, got {type(value).__name__}"))
        unknown = unknown_state_fields(value)
        if not unknown:
            return None
        return terminal_node(normalize_malformed_state(
            value, code="MALFORMED_STATE",
            message=f"unknown fields: {','.join(unknown)}"))

    @classmethod
    def _assert_known_update(cls, values: Any) -> None:
        unknown = unknown_state_fields(values)
        if unknown:
            raise StateError(f"MALFORMED_STATE:unknown fields:{','.join(unknown)}")

    def _guard_update(self, config: Any, values: Any, as_node: Any) -> None:
        """Validate the *whole merged checkpoint* an update would commit, or refuse.

        Checking field names alone accepted any value for a known key, and LangGraph's
        ``update_state`` writes straight into the checkpoint and can resume from the
        selected node without passing through VALIDATE.  So the merge is performed here,
        against the persisted state, and the result must satisfy exactly the same
        :func:`validate_state` contract the graph enforces internally -- decision state,
        phase/index coherence, every iteration budget, pending intent/event shapes and the
        terminal combination included.
        """
        if as_node is not None and as_node not in ALLOWED_UPDATE_NODES:
            raise StateError(f"MALFORMED_STATE:unknown as_node:{as_node}")
        if values is None:
            return
        if not isinstance(values, Mapping):
            raise StateError(
                f"MALFORMED_STATE:update must be a mapping, got {type(values).__name__}")
        self._assert_known_update(values)
        snapshot = _COMPILED_GRAPHS[self].get_state(config)
        current = dict(getattr(snapshot, "values", None) or {})
        merged = _merge_checkpoint(current, values)
        validate_state(merged, expected_thread_id=merged.get("thread_id", ""))

    async def _aguard_update(self, config: Any, values: Any, as_node: Any) -> None:
        """Async twin of :meth:`_guard_update`; the async ingress is guarded identically."""
        if as_node is not None and as_node not in ALLOWED_UPDATE_NODES:
            raise StateError(f"MALFORMED_STATE:unknown as_node:{as_node}")
        if values is None:
            return
        if not isinstance(values, Mapping):
            raise StateError(
                f"MALFORMED_STATE:update must be a mapping, got {type(values).__name__}")
        self._assert_known_update(values)
        snapshot = await _COMPILED_GRAPHS[self].aget_state(config)
        current = dict(getattr(snapshot, "values", None) or {})
        merged = _merge_checkpoint(current, values)
        validate_state(merged, expected_thread_id=merged.get("thread_id", ""))

    def _split_batch(self, inputs: list[Any], config: Any):
        """Partition a batch into refusals and the entries the native batch may run."""
        rejections = [self._rejection(item) for item in inputs]
        accepted = [item for item, bad in zip(inputs, rejections) if bad is None]
        accepted_config = config
        if isinstance(config, list):
            accepted_config = [cfg for cfg, bad in zip(config, rejections) if bad is None]
        return rejections, accepted, accepted_config

    @staticmethod
    def _merge_batch(rejections: list[Any], completed: list[Any]) -> list[Any]:
        remaining = iter(completed)
        return [bad if bad is not None else next(remaining) for bad in rejections]

    # ---- guarded state ingress ----

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        rejected = self._rejection(input)
        if rejected is not None:
            return rejected
        return _COMPILED_GRAPHS[self].invoke(input, config, **kwargs)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        rejected = self._rejection(input)
        if rejected is not None:
            return rejected
        return await _COMPILED_GRAPHS[self].ainvoke(input, config, **kwargs)

    def stream(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        rejected = self._rejection(input)
        if rejected is not None:
            return iter([rejected])
        return _COMPILED_GRAPHS[self].stream(input, config, **kwargs)

    async def astream(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        rejected = self._rejection(input)
        if rejected is not None:
            yield rejected
            return
        async for chunk in _COMPILED_GRAPHS[self].astream(input, config, **kwargs):
            yield chunk

    def batch(self, inputs: list[Any], config: Any = None, **kwargs: Any) -> list[Any]:
        rejections, accepted, accepted_config = self._split_batch(inputs, config)
        if not any(bad is not None for bad in rejections):
            return _COMPILED_GRAPHS[self].batch(inputs, config, **kwargs)
        completed = _COMPILED_GRAPHS[self].batch(accepted, accepted_config, **kwargs) if accepted else []
        return self._merge_batch(rejections, completed)

    async def abatch(self, inputs: list[Any], config: Any = None, **kwargs: Any) -> list[Any]:
        rejections, accepted, accepted_config = self._split_batch(inputs, config)
        if not any(bad is not None for bad in rejections):
            return await _COMPILED_GRAPHS[self].abatch(inputs, config, **kwargs)
        completed = (await _COMPILED_GRAPHS[self].abatch(accepted, accepted_config, **kwargs)
                     if accepted else [])
        return self._merge_batch(rejections, completed)

    def update_state(self, config: Any, values: Any, as_node: Any = None) -> Any:
        self._guard_update(config, values, as_node)
        return _COMPILED_GRAPHS[self].update_state(config, values, as_node=as_node)

    async def aupdate_state(self, config: Any, values: Any, as_node: Any = None) -> Any:
        await self._aguard_update(config, values, as_node)
        return await _COMPILED_GRAPHS[self].aupdate_state(config, values, as_node=as_node)

    def update_state_command(self, config: Any, command: str, *, as_node: Any = None,
                             **fields: Any) -> Any:
        """Apply one of the closed :data:`state.UPDATE_COMMANDS` instead of a raw mapping."""
        return self.update_state(config, typed_update(command, **fields), as_node=as_node)

    async def aupdate_state_command(self, config: Any, command: str, *, as_node: Any = None,
                                    **fields: Any) -> Any:
        return await self.aupdate_state(config, typed_update(command, **fields), as_node=as_node)


def build_graph(adapter: Any, *, checkpointer: Any = None, runtime_state: Any = None,
                interrupt_before: list[str] | None = None,
                interrupt_after: list[str] | None = None):
    """Compile the workflow graph.

    A durable ``RuntimeStatePort`` is **required**: EXECUTE_INTENT claims each stable intent
    before its external effect and recovers an existing receipt/settlement after a restart
    instead of creating a duplicate Task/Dispatch.  Pass it as ``runtime_state`` or bind it
    to the adapter; if neither is present this raises ``IdempotencyPortRequired`` here, at
    build time, so no graph capable of duplicating an external effect can be constructed.

    Returns a :class:`GuardedWorkflowGraph`: the compiled graph plus the closed-field check
    LangGraph cannot perform for itself.  The underlying compiled graph stays reachable as
    ``.compiled`` for tests that need to observe the unguarded behaviour.
    """
    validate_graph_spec()
    ledger = resolve_runtime_state(adapter, runtime_state)
    graph = StateGraph(WorkflowState)
    graph.add_node("VALIDATE", validate_node)
    graph.add_node("ROUTE", route_node)
    graph.add_node("ADVANCE_PHASE", advance_phase_node)
    graph.add_node("PREPARE_INTENT", prepare_intent_node)
    graph.add_node("EXECUTE_INTENT", execute_intent_node(adapter, ledger))
    graph.add_node("VALIDATE_SETTLEMENT", validate_settlement_node)
    graph.add_node("APPLY_RESULT", apply_result_node)
    graph.add_node("TERMINAL", terminal_node)
    graph.add_edge(START, "VALIDATE")
    graph.add_edge("VALIDATE", "ROUTE")
    graph.add_conditional_edges("ROUTE", lambda state: state["route_token"], ROUTE_TARGETS)
    graph.add_edge("ADVANCE_PHASE", "ROUTE")
    graph.add_edge("PREPARE_INTENT", "EXECUTE_INTENT")
    graph.add_edge("EXECUTE_INTENT", "VALIDATE_SETTLEMENT")
    graph.add_edge("VALIDATE_SETTLEMENT", "APPLY_RESULT")
    graph.add_edge("APPLY_RESULT", "ROUTE")
    graph.add_edge("TERMINAL", END)
    return GuardedWorkflowGraph(graph.compile(
        checkpointer=checkpointer, interrupt_before=interrupt_before,
        interrupt_after=interrupt_after))
