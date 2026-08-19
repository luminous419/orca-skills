# Releasing

## Version policy

`VERSION` is the single source of truth and contains a SemVer `MAJOR.MINOR.PATCH`
version. Do not repeat the numeric current version in README or installation metadata;
read it from that file when building a release.

- **MAJOR**: a breaking Skill contract or runtime-parameter semantic change.
- **MINOR**: a backward-compatible phase or policy capability, review policy, or supported runtime capability.
- **PATCH**: documentation, validator/test improvements, non-breaking prompt refinement, or bug fixes.

The project remains pre-1.0 pending the license decision and an explicit final stable
release review. Real GLM/Gemma smoke testing has been verified only in the environment
documented in `COMPATIBILITY.md`; it is not a general Orca-version support claim. Pre-1.0
minor releases may contain contract changes, which must be called out prominently in the
changelog.

## Release checklist

1. Update `VERSION` and move relevant `Unreleased` entries into a dated release section.
2. Resolve the license decision recorded in [`LICENSE-DECISION.md`](LICENSE-DECISION.md).
3. Confirm the compatibility and release-readiness statements remain accurate.
4. Run:

   ```bash
   python3 scripts/validate_skills.py
   python3 -m unittest discover -s scripts -p 'test_*.py'
   python3 scripts/verify_package.py
   python3 scripts/build_release.py
   python3 scripts/verify_package.py --archive "dist/orca-skills-$(tr -d '\n' < VERSION).tar.gz"
   git diff --check
   ```

5. Inspect the archive and publish only through an approved release process.

The archive builder normalizes ordering, file modes, ownership metadata, and timestamps.
It uses a gzip timestamp of zero, so identical source inputs produce identical bytes.

## Repository protection recommendation

Require the CI workflow to pass on pull requests before merge and avoid direct pushes to
`main`. This is a recommendation only; this repository does not modify GitHub branch
protection settings automatically.
