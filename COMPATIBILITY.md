# Compatibility and Verification Status

The repository version is read from [`VERSION`](VERSION). This document distinguishes
supported deterministic tooling from runtime configurations that have only been verified
in a specific environment.

## Compatibility matrix

| Component | Supported or verified environment | Status |
| --- | --- | --- |
| `orca-worker-reviewer-loop` | Markdown Skill package; no Orca orchestration state required | Deterministic policy and fake-agent E2E verified |
| `orca-worker-reviewer-orchestration` | Orca-native Run/Task/Dispatch lifecycle | Deterministic policy and fake-agent E2E verified |
| Repository validator and tests | CPython 3.11, 3.12, and 3.13 | Supported by CI |
| Real Orca runtime with fake agents | Orca 1.4.184 | **VERIFIED**, compatibility-gated by the opt-in Step 4 integration suite |
| `claude-glm` Worker | Distinct PATH-resolved command in the tested company environment | **VERIFIED on Orca 1.4.178-rc.2** |
| `claude-gemma` Reviewer | Separate PATH-resolved command and session in the tested company environment | **VERIFIED on Orca 1.4.178-rc.2** |
| Real GLM/Gemma smoke | Isolated company fixture on Orca 1.4.178-rc.2 | **VERIFIED**; ANALYSIS, DESIGN, IMPLEMENTATION, BUGFIX, DESIGN → IMPLEMENTATION, and FAIL → correction → PASS |

Python 3.11 is the minimum supported version. The code may run on earlier versions,
but they are outside the tested support policy. The project uses only the Python
standard library.

## Release readiness

- Deterministic policy validation: **VERIFIED**
- Fake-agent E2E: **VERIFIED**
- Real Orca with fake agents: **VERIFIED on Orca 1.4.184**
- Real GLM/Gemma smoke test: **VERIFIED on Orca 1.4.178-rc.2 in the tested company environment**
- Stable production-ready release: **NOT YET CLAIMED**

Verified environments are point observations, not a continuous supported range:

- Orca 1.4.184: deterministic real-Orca integration with fake agents.
- Orca 1.4.178-rc.2: real `claude-glm` Worker and `claude-gemma` Reviewer smoke test.

The Step 4 runtime harness remains deliberately compatibility-gated to 1.4.184. The
Step 5 environment's version-matched `orchestration` and `orca-cli` grammar contained
the required contract and the real-agent scenarios passed, but that does not establish
support for every version between these two observations. The Skill itself reads the
installed version-matched guides and does not hard-code either version as universal
command grammar.

## Real-agent lifecycle observation

The Step 5 injected Claude workers sent valid `worker_done` messages and their Tasks and
Dispatches became completed. On Orca 1.4.178-rc.2, the completed Dispatch then became
unaddressable to `worker-show` and `worker-release` (`dispatch_not_found`), while its
terminal remained idle. This differs from the 1.4.184 fake-agent harness path, where the
coordinator can release the settled worker before acknowledging its Delivery.

The lifecycle invariant is therefore expressed in two layers:

1. Account for accepted completion through the current runtime's Dispatch settlement
   contract; do not repeatedly release a Dispatch that the runtime has already settled
   and made unaddressable.
2. Independently account for any residual terminal/resource using only cleanup guidance
   from the installed version-matched guides and runtime receipts. Arbitrary process kills
   or undocumented cleanup are not acceptable.

This clarification permits observed runtime-specific settlement behavior without allowing
orphaned worker resources. Detailed evidence is in
[`STEP5_REAL_GLM_GEMMA_SMOKE_REPORT.md`](STEP5_REAL_GLM_GEMMA_SMOKE_REPORT.md).

## Stable release blockers

- **License decision:** the owner must select and add a license as documented in
  [`LICENSE-DECISION.md`](LICENSE-DECISION.md).

The lifecycle discrepancy is no longer a documentation blocker after the policy
clarification above. The strict Step 4 compatibility gate remains an intentional test-scope
constraint, not proof of a broader Orca version range. A 1.0.0 release is not declared by
this document update and still requires an explicit final release decision.
