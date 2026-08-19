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
| Real Orca runtime with fake agents | Orca 1.4.184 | Verified by the opt-in Step 4 integration suite |
| `claude-glm` Worker | Distinct PATH-resolved command | **BLOCKED / NOT YET VERIFIED** |
| `claude-gemma` Reviewer | Distinct PATH-resolved command | **BLOCKED / NOT YET VERIFIED** |

Python 3.11 is the minimum supported version. The code may run on earlier versions,
but they are outside the tested support policy. The project uses only the Python
standard library.

## Release readiness

- Deterministic policy validation: **VERIFIED**
- Fake-agent E2E: **VERIFIED**
- Real Orca with fake agents: **VERIFIED on Orca 1.4.184**
- Real GLM/Gemma smoke test: **BLOCKED / NOT YET VERIFIED**
- Stable production-ready release: **NOT YET CLAIMED**

The Orca runtime harness is deliberately compatibility-gated. A newer Orca version
must be checked against its installed version-matched `orchestration` and `orca-cli`
guides before the gate is updated.

The Step 5 blocker does not invalidate the deterministic or fake-agent results. It does
prevent promotion to a first stable release until the real commands can be resolved as
different sessions and the required analysis, design, implementation, bugfix, multi-phase,
and correction-loop scenarios have passed.
