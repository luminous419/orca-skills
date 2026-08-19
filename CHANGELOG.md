# Changelog

This project follows [Semantic Versioning](RELEASING.md). User-visible changes
are recorded here in a Keep a Changelog-inspired format.

## Unreleased

### Added

- GitHub Actions validation across the supported Python versions.
- Release metadata, compatibility documentation, and distributable-package verification.

### Verified

- Real `claude-glm` Worker and `claude-gemma` Reviewer smoke testing in an isolated
  company environment on Orca 1.4.178-rc.2.
- ANALYSIS, DESIGN, IMPLEMENTATION, BUGFIX, DESIGN → IMPLEMENTATION, and an actual
  Reviewer FAIL → Worker correction → Reviewer PASS path.

### Changed

- Clarified lifecycle accounting when a runtime auto-settles a completed Dispatch before
  explicit worker release, including separate accounting for residual terminal resources.

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
