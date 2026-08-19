# Changelog

This project follows [Semantic Versioning](RELEASING.md). User-visible changes
are recorded here in a Keep a Changelog-inspired format.

## Unreleased

### Added

- GitHub Actions validation across the supported Python versions.
- Release metadata, compatibility documentation, and distributable-package verification.

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

- Real `claude-glm` Worker and `claude-gemma` Reviewer smoke testing remains blocked because those PATH commands were unavailable in the Step 5 environment.
- A stable production-ready release is not yet claimed.
