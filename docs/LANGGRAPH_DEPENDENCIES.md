# LangGraph dependency contract (OS-40)

The deterministic engine is tested on Python 3.11 with the exact optional runtime set in
`requirements-langgraph.txt`: LangGraph 0.2.76, checkpoint 2.1.1, SDK 0.1.74,
langchain-core 0.3.80, and langsmith 0.3.45. LangGraph declares core `>=0.2.43,<0.4.0`,
checkpoint `>=2.0.10,<3`, and SDK `>=0.1.42,<0.2`; this repository supports only the exact
tested set until another set passes the full gates.

Legacy validators and pure contracts remain standard-library-only. The executable graph
fails explicitly when LangGraph is absent; it never falls back to the old prompt loop.

CI runs this contract as two lanes, both across Python 3.11, 3.12 and 3.13
(`.github/workflows/ci.yml`, enforced by `scripts/ci_lane.py`). The `langgraph-present`
lane installs `requirements-langgraph.txt` and refuses to run if the install did not take
effect, so graph, repair, crash/restart and replay coverage can never silently skip. The
`langgraph-absent` lane installs nothing and asserts the degraded behaviour described in
the paragraph below -- it passes intentionally, and its skips are held against an IDENTITY
SET, not a count: `scripts/langgraph_skip_manifest.txt` names every test expected to skip
without the runtime, the absent lane requires that set to be skipped exactly (no missing
entry, no extra one), and the present lane requires every one of those tests to have
executed. Every OTHER skip is held to the same kind of contract by
`scripts/tolerated_skip_manifest.txt`, which declares each one as (test id, exact reason)
and is matched exactly in both lanes -- so an extra test carrying an already-declared
reason fails, not just a differently worded one. A count or a reason pattern would drift
silently as tests are added and removed; the identity sets make each such change show up as
a manifest diff. The pinned set above was checked to resolve, install and
import on all three declared versions; 3.11 remains the version the full OS-40 runtime
verification ran on.

LangSmith is a transitive Python dependency of langchain-core. No LangSmith tracing,
LangChain Agent, Agent Server, or hosted service is used.

| Distribution | Version | License observed in installed metadata/files |
| --- | --- | --- |
| langgraph | 0.2.76 | MIT |
| langgraph-checkpoint | 2.1.1 | MIT |
| langgraph-sdk | 0.1.74 | MIT |
| langchain-core | 0.3.80 | MIT |
| langsmith | 0.3.45 | MIT |
| ormsgpack / orjson | 1.10.0 / 3.11.3 | MIT OR Apache-2.0 |
| httpx | 0.28.1 | BSD-3-Clause |
| requests | 2.32.5 | Apache-2.0 |

These dependency licenses do not select a license for this repository; the owner decision
in `docs/LICENSE-DECISION.md` remains open. Production durable checkpointer selection is OS-31.
