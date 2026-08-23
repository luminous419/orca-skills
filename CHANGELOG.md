# Changelog

This project follows [Semantic Versioning](RELEASING.md). User-visible changes
are recorded here in a Keep a Changelog-inspired format.

## Unreleased

### Added

- GitHub Actions validation across the supported Python versions.
- Release metadata, compatibility documentation, and distributable-package verification.
- Machine-checkable lifecycle accounting contract in the orchestration skill, enforced by the repository validator and covered by negative regression tests.
- Project Quality Profile (`.orca/quality-profile.yaml`) with a standard-library loader, strict schema validation, and per-phase applicability filtering, rendered into the dispatched Worker and Reviewer Task specs as a `=== QUALITY GATE (profile-first) ===` block, plus a generic example profile and a machine-checkable quality profile contract enforced by the repository validator.
- Implicit Final Adversarial Review gate in the orchestration skill: after every requested phase set a fresh Reviewer session reviews the whole final tree, findings carry a `Responsible Phase` that routes each correction back to its owning phase, and a machine-checkable final review contract block is enforced by the repository validator.
- Run-scoped `ORCHESTRATOR_LOG.md` and `TIMING_LOG.md` under `artifacts/runs/<run-id>/`, written by a shared `scripts/run_logging.py` writer from the actual orchestration execution path: `OrcaRuntimeHarness` calls it directly for every run start/end, Worker/Reviewer/Final-Review dispatch settlement, unexpected exit, and pre-dispatch failure, and a Coordinator driving Orca by hand can call the same logic through a small CLI (`python3 scripts/run_logging.py orchestrator-event|timing-event|run-status`). Phase and iteration boundaries (`log_phase_start`/`log_phase_end`/`log_iteration_start`/`log_iteration_end`, and the matching `timing-event` call points for a live Coordinator) get their own authoritative `TIMING_LOG.md` rows instead of requiring a reader to reconstruct them by grouping `dispatch_settled` rows.

### Verified

- Real `claude-glm` Worker and `claude-gemma` Reviewer smoke testing in an isolated
  company environment on Orca 1.4.178-rc.2.
- ANALYSIS, DESIGN, IMPLEMENTATION, BUGFIX, DESIGN → IMPLEMENTATION, and an actual
  Reviewer FAIL → Worker correction → Reviewer PASS path.

### Changed

- Reviewer verdicts are decided profile-first instead of against a broad generic quality checklist: explicit requirements, then applicable project quality attributes, then the current phase contract, then a Minimal General Gate of exactly five categories. Generic best practice, naming taste, minor duplication and similar preferences are no longer grounds for `FAIL` unless the project profile declares them blocking.
- Findings now carry `Quality Attribute` and `Blocking` alongside `Severity`; severity expresses impact and no longer decides the gate on its own. Review reports add a `REVIEW_VERDICT` annotation with `PASS`, `PASS WITH NOTES`, `FAIL` and `BLOCKED`, while the workflow gate stays the two-valued `RESULT: PASS | FAIL` — no new lifecycle state, and task settlement, the FAIL loop, downstream revalidation and the Final Review trigger are unchanged.
- A missing quality profile is a normal state that uses requirements, the phase contract and the Minimal General Gate only; a profile that exists but does not validate is a pre-dispatch validation failure reported as `STATUS: BLOCKED` / `REASON: INVALID_QUALITY_PROFILE`, never a silent fallback to the generic checklist.
- The quality profile is resolved once per run, at the run boundary, and the same immutable resolution is threaded through every Worker, phase Reviewer, correction, downstream revalidation and Final Reviewer spec of that run; a profile edited mid-run can no longer hand a Reviewer a different quality model than the Worker it is reviewing. Only a genuinely nonexistent path is `absent` — a path that exists but is not a readable regular file (a directory or broken symlink at `.orca/quality-profile.yaml`) is `invalid`.
- `STATUS: COMPLETED` in the orchestration skill now requires both every requested phase PASS and a Final Adversarial Review PASS; a phase PASS alone no longer completes a run, and final review attempts are bounded by their own iteration counter, separate from the per-phase one.
- Clarified lifecycle accounting when a runtime auto-settles a completed Dispatch before
  explicit worker release, including separate accounting for residual terminal resources.
- Replaced the two-layer lifecycle account with four independent axes — settlement,
  supervised worker-resource registration, residual process liveness, and cleanup
  authority — and added `unsupervised` as an explicit fourth worker-resource outcome so a
  dispatch that was never registered is no longer released repeatedly.
- Required each Dispatch to be finalized exactly once, behind a gate that runs before any
  lifecycle action rather than after it.
- Required the phase Task graph to be created before the Worker is dispatched, so the
  Reviewer Task becomes ready by dependency promotion; manual readiness override is now
  recovery-only.
- Made terminal close depend on a close-eligible terminal role plus proven ownership. The
  coordinator's own session, setup terminals, adopted terminals, and still-active workers
  can never be closed.
- Documented the supervised-first placement ladder for custom PATH wrapper commands, so
  the "unconfigured agent" response is treated as a branch signal rather than a failure.

## 0.9.0 - 2026-08-20

### Added

- Direct-session and Orca-native Worker/Reviewer skills with shared phase and review policy.
- Structural validation for frontmatter, phase routing, policy gates, and cross-skill parity.
- Machine-readable policy contracts and deterministic policy smoke tests.
- Dedicated BUGFIX review policy and concise, reviewer-centered phase prompts.
- Deterministic fake-agent E2E coverage for workflow state transitions and finding continuity.
- Opt-in real Orca runtime integration coverage using deterministic fake agents, verified on Orca 1.4.184.

### Changed

- Clarified worker settlement, reuse, retain, and release responsibilities for Orca-native orchestration.

### Known limitations

- Verified Orca 1.4.178-rc.2 and 1.4.184 environments are separate point observations,
  not a supported version range.
- A stable production-ready release is not yet claimed; the license decision remains open.
