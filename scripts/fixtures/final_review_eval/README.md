# Final Review evaluation fixture

A small, self-contained subject project in two states, used to exercise a Final
Adversarial Review end to end against a known target rather than against whatever
happens to be in the repository that week.

```text
subject/
    base/     the project before the feature under review
    head/     the same project after it
```

Both trees carry the same file list: a written `CONTRACT.md`, six `src/` modules and a
`tests/` suite. **Both suites pass.** The feature under review is per-destination
retention tiers: `head` adds a `TIERS` table, a tier-resolution step, a `destination`
source in the settings ladder, a `tier=` parameter on quota enforcement, and a
`republish` retry entry point.

## Two trees, not a stored patch

Section 17 hands a Final Reviewer the whole `base..HEAD` diff by path, so a reviewable
diff is part of this fixture's realism rather than an extra. The diff is **derived** at
materialization time with `difflib.unified_diff`, so it cannot drift from the trees, and
storing it is deliberately avoided: a stored patch from one state to another would be a
description of the change rather than the change itself, which is not what a real review
receives.

## Materializing a workspace

```bash
python3 scripts/final_review_eval.py materialize --dest <dir>
```

Produces `CONTRACT.md`, `src/`, `tests/`, a generated `DIFF.patch` (`base` → `head`) and
a `MANIFEST.json` of per-file digests. The destination must be empty; the command
refuses to overwrite, merge or partially reuse one.

No `.git` is created and none is copied. The reviewer gets `DIFF.patch`, which is what
section 17 hands it anyway, and the workspace carries no history to mine.

## Regenerating

`MANIFEST.json`'s `fixture_digest` is a `sha256` over the sorted
`"<relpath>\0<sha256-hex>\n"` manifest text, so it is stable across filesystems and
directory orderings. Changing anything under `subject/head/` changes it, and
`materialize` then refuses to run until the recorded expectation is updated by hand.
There is deliberately no flag that updates the expectation automatically — a check that
rewrites the value it is checking against is not a check.

## Scope

This fixture is an evaluation input. It is not a test of this repository's own code, it
is not imported by anything under `scripts/`, and its `subject/` tree shares no
vocabulary with orchestration: no run, dispatch, reviewer, attempt, phase, verdict or
artifact-root concept appears anywhere in it.

## Reproducing a §7 baseline

`materialize` alone builds a clean *tree*; it does not constrain the *process* that reads
it. A capture that is to be called a §7 baseline is dispatched through `isolate`, which
builds an ephemeral session containing only the materialized subject and a closed list of
review-policy files, proves every path the reviewer can read is either exhaustively scanned
for key material or exhaustively proven immutable, generates a kernel-enforced scope
profile that denies the key-bearing roots for content *and* metadata, and records the
result in `ISOLATION.json`:

```bash
python3 scripts/final_review_eval.py isolate --run-id <run> --enforcement seatbelt
# ... dispatch one Final Review attempt into <SESSION>/review_root ...
python3 scripts/final_review_eval.py isolate --repatriate <SESSION> --run-id <run>
python3 scripts/final_review_eval.py isolate --teardown <SESSION>
```

`--enforcement none` is the only supported way to run without a backend, and a capture
recorded that way **fails B6 and is not a baseline** — it is an exploratory run and is
labelled as one.
