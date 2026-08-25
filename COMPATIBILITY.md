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

Two further observations refine that report's interpretation. First, an accepted
`worker_done` settles the Dispatch regardless of how the worker was started: a low-level,
tracked Dispatch is not exempt from auto-settlement. Second, `dispatch_not_found` is
returned both before and after settlement, so it is evidence about the supervised
worker-resource registry and not about settlement in either direction.

The lifecycle invariant is therefore expressed as four independent axes, each with its
own recorded outcome:

1. **(a) Settlement** — accepted completion read from Task/Dispatch provenance. Do not
   repeatedly release a Dispatch the runtime has already settled.
2. **(b) Supervised worker-resource registration** — `reuse`, `retain`, `release`, or
   `unsupervised` when the dispatch was never registered as a supervised worker resource.
3. **(c1) Residual process liveness** — checked always, from terminal inspection, and
   answering only whether a process is still alive.
4. **(c2) Cleanup authority** — `authorized` only when a close-eligible terminal role and
   proven ownership both hold. Liveness never grants it, and self-creation alone never
   grants it. Anything else is retain-and-report.

Splitting (c1) from (c2) is what keeps a live terminal from being read as permission to
close it, and the terminal role gate is what keeps the coordinator's own session, setup
tabs, and adopted terminals permanently out of the close path. Residual terminals are
still cleaned up only through the installed version-matched guides and runtime receipts;
arbitrary process kills or undocumented cleanup remain unacceptable. Detailed evidence is
in [`STEP5_REAL_GLM_GEMMA_SMOKE_REPORT.md`](STEP5_REAL_GLM_GEMMA_SMOKE_REPORT.md).

## Agent Profile

Agent Profile is an optional abstraction and requires no Orca version beyond what the two
skills already need. It changes which agent command each phase runs, not how Orca is
driven: no new orchestration or CLI verb is used, no argument is added to an agent launch,
and the Run/Task/Dispatch lifecycle is unchanged.

An invocation without `profile=` behaves exactly as it did before the feature existed —
the profile files are not read, so a malformed or unreadable `~/.orca/agent-profiles.yaml`
cannot affect a run that does not ask for a profile.

Profile files are plain data read with the repository's own restricted-subset YAML reader;
no third-party dependency is introduced.

## Final Review audit records and evaluation tooling

The per-dispatch Final Adversarial Review audit records
(`artifacts/runs/<run-id>/final_review_audit/`), the evidence-bundle export, the evaluation
fixture and the scorer require no Orca version beyond what the two skills already need, and
introduce no third-party dependency: standard library only, CPython 3.11+.

**Additive by construction.** They add a directory under an existing artifact root, new
`--event` values in a vocabulary that was already open (no `ORCHESTRATOR_LOG.md` column is
added, so every file on disk keeps its width), new functions and subcommands in the shared
`run_logging.py`, and a new repository-side `scripts/final_review_eval.py`. No existing
column, path, schema or function signature changes. `RESULT:` stays two-valued and
`REVIEW_VERDICT:` stays four-valued — both are copied verbatim into a record, never
re-derived or collapsed.

**No migration, and none is possible to need.** No existing artifact changes meaning, so no
existing consumer can misread one. A run that completed before these records existed simply
has no `final_review_audit/` directory, and every reader treats an absent record as
`unknown` — the correct reading for a run that never wrote one. Historical runs are not
backfilled, deliberately: a backfilled record would carry a `recorded_at` that is not the
settlement time and a report snapshot taken long after any overwrite, which is precisely the
stale self-referential provenance these records exist to prevent.

**Capture degrades, it does not fail.** The two capture sources are post-dispatch `orca`
CLI reads. If `orca` is absent from `PATH`, exits non-zero, times out or returns
unparseable JSON, the record is still written with `capture_status: unavailable` and a
non-empty `capture_error` — a record that says why the input could not be captured is
evidence; a missing record is not.

**Redaction policy v1.0 covers POSIX paths only.** `C:\Users\<name>` is deliberately not a
category: this document does not claim Windows support for the runtime path, and an untested
pattern is worse than a stated gap. Adding it is a MINOR policy bump.

**Packaging.** `scripts/` is included in the release archive, so a downloaded tarball
contains `scripts/fixtures/final_review_eval/key/answer_key.json`. This is stated rather
than worked around: the claim the tooling makes is about the reviewer's *retained input*,
verified mechanically per run, not "the key was unreachable."

## Stable release blockers

- **License decision:** the owner must select and add a license as documented in
  [`LICENSE-DECISION.md`](LICENSE-DECISION.md).

The lifecycle discrepancy is no longer a documentation blocker after the policy
clarification above. The strict Step 4 compatibility gate remains an intentional test-scope
constraint, not proof of a broader Orca version range. A 1.0.0 release is not declared by
this document update and still requires an explicit final release decision.
