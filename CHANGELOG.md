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
- Risk-based workflows in the orchestration skill: a new `risk=low|medium|high` runtime parameter that is a second axis beside `phases` — `phases` decides WHAT runs, `risk` decides HOW STRONGLY the requested phases are validated, and risk never expands or contracts the requested phase set. `LOW` runs each requested phase Worker-only over a single-node Task graph (no dependent Reviewer node is created at all, so none is promoted to ready and then abandoned); `MEDIUM` runs today's Worker → phase-Reviewer gate with its bounded correction loop; `HIGH` is today's full strength including section 17's T5a downstream revalidation. The Final Adversarial Review is mandatory and identical at every level. `risk` defaults to `high`, so an invocation that omits it behaves exactly as before. The semantics and a machine-checkable `#### Risk profile contract` anchor block live in SKILL.md section 8, enforced by the repository validator and loaded by a single parser (`scripts/skill_policy.py`'s `load_risk_contract()`) that `scripts/validate_skills.py` imports rather than re-implements. The block is orchestration-only and is asserted absent from `orca-worker-reviewer-loop`, which keeps no risk axis and is untouched. The resolved risk reaches Workers and Reviewers as a `=== RISK PROFILE ===` block in the dispatched Task spec, alongside but strictly independent of `=== QUALITY GATE (profile-first) ===` — the two builders share no argument and no key. `ORCHESTRATOR_LOG.md` gained `risk`, `risk_source`, `requested_phases` and `round_kind` columns, `TIMING_LOG.md` gained `risk`, and a `reviewer_gate_skipped` event records a skipped gate positively so the absence of a row is never the only evidence.
- Run-scoped `ORCHESTRATOR_LOG.md` and `TIMING_LOG.md` under `artifacts/runs/<run-id>/`, written by a shared `run_logging.py` writer from the actual orchestration execution path: `OrcaRuntimeHarness` calls `scripts/run_logging.py`'s functions directly for every run start/end, Worker/Reviewer/Final-Review dispatch settlement, unexpected exit, and pre-dispatch failure, and a Coordinator driving Orca by hand can call the same logic through a small CLI shipped inside the installed Skill itself (`python3 <SKILL_DIR>/tools/run_logging.py orchestrator-event|timing-event|run-status`) -- the two copies are byte-identical (enforced by `scripts/validate_skills.py`) since a global/project-local Skill install never copies this repository's `scripts/`. Phase and iteration boundaries get their own authoritative `TIMING_LOG.md` rows instead of requiring a reader to reconstruct them by grouping `dispatch_settled` rows: `OrcaRuntimeHarness` derives them automatically from the `(phase, iteration)` transitions it observes on every dispatch it settles (no separate call for a scenario to omit), and SKILL.md gives a live Coordinator the matching `timing-event --event phase_start|phase_end|iteration_start|iteration_end` call points. `ORCHESTRATOR_LOG.md` gained two columns for a Reviewer/Final Reviewer dispatch's own settled response, neither of which `result`'s `outcome=succeeded` can answer on its own (a Reviewer settles just as successfully on FAIL as on PASS): `gate_result`, the two-valued `RESULT: PASS`/`RESULT: FAIL` workflow gate, and `review_verdict`, OS-1's separate four-valued `REVIEW_VERDICT: PASS`/`PASS WITH NOTES`/`FAIL`/`BLOCKED` report annotation, which `gate_result` alone would collapse (PASS WITH NOTES into PASS, BLOCKED into FAIL). `log_timing_event()`/`timing-event` now derive `duration_s` from `started_at`/`ended_at` automatically when the caller (in practice, always the CLI path) doesn't supply it, instead of leaving it blank.

### Fixed

- `TIMING_LOG.md` no longer records negative durations or out-of-order timestamps (OS-19). PR #16's real OS-3 run left five `dispatch_settled` rows whose `duration_s` was negative (-423s, -2267s, -1296s, -1998s, -2766s) and whose `started_at` was minutes *after* their own `ended_at`, plus `iteration_end`/`phase_end` rows with an empty `started_at` and a blank duration. The cause was that the CLI path had no clock and no memory: SKILL.md section 9 asked a Coordinator for `--started-at <dispatch 직전 시각>`, and a Coordinator with no clock chains each dispatch's start off the previous row's (itself estimated, often future-dated) end. The fix is an authoritative time source and a real boundary lifecycle on that path, not a clamp. A new `timing-dispatch-start` subcommand reads the clock itself immediately before `worker-start`, keeps the captured instant until that dispatch settles, and opens the dispatch's phase/iteration boundary at that same instant -- closing whatever scope it replaces at *that* scope's own last settlement, so the next scope's time can never land in the previous one's duration. `timing-event --event dispatch_settled` therefore takes no timestamps at all, and `phase_end`/`iteration_end` need no `--started-at`. The boundary lifecycle and the dispatch clock now live in one shared `run_logging.RunTimingTracker` that both paths use -- in memory for `OrcaRuntimeHarness`, as a JSON file beside the two logs for the CLI -- so the Python runtime path and the installed Skill CLI path cannot drift into different timing semantics. Where a timestamp pair still cannot yield a measurement (an `ended_at` before its `started_at`, an unparsable or mixed-awareness value, an explicitly supplied negative `--duration-seconds`, or an explicitly supplied non-finite one -- `nan`, `inf`, `-inf`, none of which a `< 0` test can catch since every comparison against NaN is false), `duration_s` is left empty and the row's `detail` gains a `timing_invalid=...` marker: never clamped to `0` and never absolute-valued. That judgement is not skipped when the caller also supplies `--duration-seconds` -- a populated timestamp pair is validated on its own, and a duration computed for an impossible pair is not a measurement of it. The rejected pair does not reach the `started_at`/`ended_at` columns either, since a row carrying it would itself violate `started_at <= ended_at` for anyone reading those columns without the `detail`; the two values move into the same row's `detail` as `timing_invalid_started_at=<input> timing_invalid_ended_at=<input>` instead, because what arrived is the evidence. Every emitted row therefore holds the invariant on its face: a timestamp column contains a readable timestamp or nothing, and a populated pair is always ordered. `elapsed_seconds()` also stopped raising `TypeError` on an offset-naive/offset-aware pair -- the harness computed the duration at its own call site, *outside* `_safe_log`, so that exception escaped logging and aborted an already-settled Dispatch, which section 9 forbids; the derivation now happens once, inside the writer.

### Verified

- Real `claude-glm` Worker and `claude-gemma` Reviewer smoke testing in an isolated
  company environment on Orca 1.4.178-rc.2.
- ANALYSIS, DESIGN, IMPLEMENTATION, BUGFIX, DESIGN → IMPLEMENTATION, and an actual
  Reviewer FAIL → Worker correction → Reviewer PASS path.

### Changed

- An explicitly supplied risk value that is not a level fails closed as `STATUS: BLOCKED` / `REASON: INVALID_RISK` before any Run, Task or Dispatch is created — the same pre-dispatch validation shape as `INVALID_PHASE` and `INVALID_QUALITY_PROFILE`. An explicitly empty `risk=` is that case, not an omission: the tokenizer recognizes the key with or without a value, so it can never be silently logged as `risk_source=default` for a run where the parameter was actually written. Uppercase and mixed-case values are case-folded.
- The section 6 Task graph is now risk-conditional: risk selects which nodes exist, never when they are created. The whole graph is still built before the Worker is dispatched, and a dependent is still never created after its dependency completes, at every risk level.
- `PHASE_ITERATIONS[p]` is redefined as phase *p*'s gate attempts — a Reviewer attempt at MEDIUM/HIGH (unchanged meaning) and a Worker attempt at LOW — so the per-phase budget and `MAX_ITERATIONS_REACHED (phase p)` stay reachable at every level.
- Section 14's mandatory test gates now state explicitly that they hold regardless of risk: risk changes validation strength, never the safety floor. At LOW, where no phase Reviewer exists, the Worker must carry an affirmative `UNIT_TEST_STATUS: PASS` on IMPLEMENTATION / BUGFIX / REFACTORING; a missing, duplicate, malformed or `BLOCKED` value is a non-PASS phase gate with its own reason.
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
