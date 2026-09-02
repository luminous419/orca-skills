"""Single declarative graph topology and static validation."""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import ROUTE_TOKENS

NODES = ("VALIDATE", "ROUTE", "ADVANCE_PHASE", "PREPARE_INTENT", "EXECUTE_INTENT",
         "VALIDATE_SETTLEMENT", "APPLY_RESULT", "TERMINAL")
STATIC_EDGES = (
    ("VALIDATE", "ROUTE"), ("ADVANCE_PHASE", "ROUTE"),
    ("PREPARE_INTENT", "EXECUTE_INTENT"),
    ("EXECUTE_INTENT", "VALIDATE_SETTLEMENT"),
    ("VALIDATE_SETTLEMENT", "APPLY_RESULT"), ("APPLY_RESULT", "ROUTE"),
)
ROUTE_TARGETS = {
    "BLOCK": "TERMINAL", "ESCALATE": "TERMINAL", "COMPLETE": "TERMINAL",
    "ADVANCE_PHASE": "ADVANCE_PHASE", "PREPARE_WORKER": "PREPARE_INTENT",
    "PREPARE_PHASE_REVIEWER": "PREPARE_INTENT", "PREPARE_FINAL_REVIEWER": "PREPARE_INTENT",
    "PREPARE_CORRECTION": "PREPARE_INTENT", "PREPARE_REVALIDATION": "PREPARE_INTENT",
}
CYCLE_GUARDS = frozenset({"phase_budget", "final_budget", "phase_index_monotonic"})


class GraphSpecError(ValueError): pass


@dataclass(frozen=True)
class GraphSpec:
    nodes: tuple[str, ...] = NODES
    edges: tuple[tuple[str, str], ...] = STATIC_EDGES
    route_targets: tuple[tuple[str, str], ...] = tuple(ROUTE_TARGETS.items())
    cycle_guards: frozenset[str] = CYCLE_GUARDS


def validate_graph_spec(spec: GraphSpec = GraphSpec()) -> None:
    nodes = set(spec.nodes); targets = dict(spec.route_targets)
    if set(targets) != set(ROUTE_TOKENS): raise GraphSpecError("conditional route coverage mismatch")
    if any(a not in nodes or b not in nodes for a, b in spec.edges): raise GraphSpecError("unknown edge target")
    if any(target not in nodes for target in targets.values()): raise GraphSpecError("unknown route target")
    if any(a == "TERMINAL" for a, _ in spec.edges): raise GraphSpecError("terminal has outgoing edge")
    adjacency = {n: set() for n in nodes}
    adjacency["ROUTE"].update(targets.values())
    for a, b in spec.edges: adjacency[a].add(b)
    seen = {"VALIDATE"}; stack = ["VALIDATE"]
    while stack:
        for nxt in adjacency[stack.pop()]:
            if nxt not in seen: seen.add(nxt); stack.append(nxt)
    if seen != nodes: raise GraphSpecError(f"unreachable nodes: {sorted(nodes-seen)}")
    reverse = {n: set() for n in nodes}
    for a, values in adjacency.items():
        for b in values: reverse[b].add(a)
    reaching = {"TERMINAL"}; stack = ["TERMINAL"]
    while stack:
        for prev in reverse[stack.pop()]:
            if prev not in reaching: reaching.add(prev); stack.append(prev)
    if reaching != nodes: raise GraphSpecError(f"dead-end nodes: {sorted(nodes-reaching)}")
    if spec.cycle_guards != CYCLE_GUARDS: raise GraphSpecError("cycle guard metadata mismatch")
