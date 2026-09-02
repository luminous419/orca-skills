# Runtime-neutral deterministic workflow engine

OS-40 moves phase routing, gates, bounded correction, downstream revalidation, and final
review completion into `scripts/deterministic_workflow`. `graph.py` builds the sole executable
LangGraph `StateGraph`; `routing.py` contains the pure functions used by its conditional edge.
Adapters execute stable action intents and return separately bound settlement events.

## Checkpoint and idempotency

`PREPARE_INTENT` completes before the static edge to `EXECUTE_INTENT`, so a LangGraph
checkpointer records the intent before any external effect. Command, intent, and event IDs are
SHA-256 digests of canonical JSON identities. Adapter receipt lookup makes a node replay return
the original effect, while the graph's processed event IDs prevent duplicate budget use.
Process/terminal handles, credentials, clients, bytes, Paths, and unknown state fields are
rejected from checkpoint state.

## Ports and adapters

`ports.py` defines agent execution, artifact, runtime receipt, existing OS-30 human approval,
clock, and ID protocols. `fake_adapter.py` provides Orca-independent deterministic execution.
`orca_adapter.py` composes OrcaRuntimeHarness-compatible execution primitives, strips runtime
handles from settlements, and owns no routing rules. Missing declared capabilities block before
dispatch.

## Migration matrix

| Former owner | Graph owner | Disposition |
| --- | --- | --- |
| Skill phase/FAIL/iteration/final-review prose | graph + pure router | prose becomes explanatory contract |
| `e2e_harness.run_workflow` | graph | test-only parity oracle for one compatibility release |
| `downstream_revalidation_set` | pure router | canonical HIGH-only suffix calculation |
| OrcaRuntimeHarness Task/Dispatch/lifecycle | Orca adapter | execution/provenance only |
| OS-28/29 decision contracts | existing modules | reused unchanged |
| OS-30 clarification v2 | existing HumanApprovalPort | publish and terminal block only |

## Extension points

OS-31 may inject a durable LangGraph checkpointer and durable `RuntimeStatePort`, then add a
`WAITING_FOR_INPUT` node which consumes OS-30 responses. OS-40 uses MemorySaver for reconstruction
tests and terminates NEEDS_INPUT/CONFLICT as BLOCKED. OS-37 implements `AgentExecutionPort` with
structured argv, durable idempotency receipts, ownership, and capability declarations; the graph
does not change. OS-38 source extraction remains out of scope.
