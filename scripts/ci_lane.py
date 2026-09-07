"""The two CI lanes, and the assertion each one owes.

OS-42 review MAJOR 2. `.github/workflows/ci.yml` had no `pip install` step at all, so
every job ran with LangGraph absent and the graph, repair, crash/restart and replay tests
silently skipped -- 259 of them. The suite still reported "OK (skipped=259)", which is
indistinguishable from success unless somebody counts. The PR's own numbers had been
measured on a developer machine where LangGraph *is* installed, so nothing in the pipeline
could notice the difference.

The fix is not "install LangGraph". It is that each lane must be NAMED and must ASSERT the
condition it claims to test:

``present``
    The pinned runtime is installed, the engine imports, NO test is skipped for a LangGraph
    reason, and every test in ``LANGGRAPH_SKIP_MANIFEST`` actually EXECUTED. A silently
    failed `pip install` fails the job instead of quietly reverting it to the other lane,
    and a gated test that was deleted or renamed away cannot satisfy "no langgraph skips".

``absent``
    The pinned runtime is genuinely missing, the documented degraded behaviour holds
    (named refusals, not crashes), and the suite PASSES -- with the LangGraph-skipped set
    EQUAL to ``LANGGRAPH_SKIP_MANIFEST``.

EVERY skip is held to an IDENTITY contract, in both directions, in both lanes. There are
two manifests and no third mechanism:

``LANGGRAPH_SKIP_MANIFEST``
    the tests that skip because the runtime is absent -- skipped exactly in the absent
    lane, executed in the present lane.

``TOLERATED_SKIP_MANIFEST``
    every other skip, declared as (test id, exact reason) -- matched exactly in BOTH lanes.

Neither is a count and neither is a reason pattern. Two review rounds landed on the same
defect from different sides. Round 1: the absent lane rejected only the zero case, so 246
langgraph skips passed and so would 500. Round 2: the LangGraph half had been made strict
but the other half was still `any(pattern.search(reason) ...)`, under a comment claiming
its six live-runtime skips were "enumerated" and that a seventh would fail -- when in fact
a seventh with identical wording passed, and so would five hundred.

The generalisation, which is the point rather than either instance: if a lane tolerates a
skip, the exact set of tolerated TESTS is declared and compared both ways. Wording is
evidence, never authority. A count drifts silently -- delete one gated test, add another,
and the total is unchanged while the covered surface is not.

Both lanes forbid failures and errors. Neither lane is allowed to be satisfied by the
other's condition, which is what makes running both of them evidence.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = REPO_ROOT / "requirements-langgraph.txt"

#: The environment variable a CI job sets to declare which lane it is.
LANE_ENV = "ORCA_CI_LANGGRAPH_LANE"
LANE_PRESENT = "present"
LANE_ABSENT = "absent"
LANES = (LANE_PRESENT, LANE_ABSENT)

#: Skip reasons a test may legitimately carry BECAUSE the pinned runtime is absent.
#: Matched against the reason text unittest records, so a gate that invents a new wording
#: is reported as unaccounted rather than waved through.
LANGGRAPH_SKIP_PATTERNS = (
    re.compile(r"langgraph", re.IGNORECASE),
)

#: Every skip that is NOT a LangGraph skip, declared by IDENTITY and by exact REASON.
#:
#: Review round 2 raised F-001 against the previous version of this, which was a tuple of
#: regexes consulted by `any(pattern.search(reason) ...)`. Its comment claimed the six
#: live-runtime skips were "enumerated" and that "a seventh ... still fails the lane".
#: Neither was true: there was no count and no identity, so a seventh skip with the SAME
#: wording passed, and so would five hundred. That is the same defect as the original
#: external review's -- incidental skips masquerading as success -- surviving in the half
#: of the contract nobody had tightened, behind a comment that said otherwise.
#:
#: What the code below now ACTUALLY enforces, in BOTH lanes: the set of (test id, reason)
#: pairs for non-LangGraph skips must EQUAL `TOLERATED_SKIP_MANIFEST` exactly. A MISSING
#: entry fails. An EXCESS entry fails -- a seventh live-runtime skip included, because it
#: is a different test id even though the wording matches. The same test skipping for a
#: DIFFERENT reason fails, and is reported as a reason mismatch rather than as an
#: unrelated missing/excess pair.
#:
#: Deliberately NOT declared here, so that they fail the lane if they ever occur: the
#: git-availability skips in the retained-report whitespace gate. A CI runner without git,
#: or a shallow checkout, means that gate silently is not running -- which is precisely the
#: class of defect this contract exists to surface, so it must be loud rather than
#: tolerated.
TOLERATED_SKIP_MANIFEST = REPO_ROOT / "scripts" / "tolerated_skip_manifest.txt"

TOLERATED_MANIFEST_HEADER = """\
# Every skip the suite may produce that is NOT a LangGraph skip, as
# `<test id><TAB><exact skip reason>`.
#
# Generated by `python3 -m scripts.ci_lane write-manifest --lane absent`. Both lanes
# require this set to match EXACTLY: a missing entry, an extra one, or the same test
# skipping with different wording each fail the lane.
#
# These are the opt-in live-runtime tests, gated on ORCA_RUNTIME_TEST=1, which no CI
# runner sets. Nothing else may skip. In particular the git-availability skips in the
# retained-report whitespace gate are deliberately absent: if that gate ever stops running
# because git is missing or the checkout is shallow, the lane must go red, not quietly
# tolerate it.
"""


def load_tolerated_skips(path: Path | None = None) -> frozenset[tuple[str, str]]:
    """The declared (test id, reason) pairs. Blank lines and `#` comments are ignored."""
    source = path or TOLERATED_SKIP_MANIFEST
    pairs = set()
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        test_id, _, reason = line.partition("\t")
        pairs.add((test_id.strip(), reason.strip()))
    return frozenset(pairs)


#: The EXACT set of tests that are expected to skip for a LangGraph reason in the
#: dependency-absent lane, one `unittest` test id per line. This is a checked-in
#: declaration, deliberately independent of what the run happens to produce -- comparing
#: the run against something derived from the same gates would be circular and would
#: assert nothing.
#:
#: Regenerate with `python3 -m scripts.ci_lane write-manifest` from an environment where
#: LangGraph is genuinely absent, and read the diff before committing it: a line
#: DISAPPEARING means a test stopped needing the runtime (or was deleted), and a line
#: APPEARING means coverage moved out of the dependency-absent lane. Both are decisions,
#: not bookkeeping.
LANGGRAPH_SKIP_MANIFEST = REPO_ROOT / "scripts" / "langgraph_skip_manifest.txt"

MANIFEST_HEADER = """\
# The tests that skip because the pinned LangGraph runtime is absent.
#
# Generated by `python3 -m scripts.ci_lane write-manifest --lane absent` from an
# environment without the distribution installed. Do not hand-edit to make a lane pass:
# a line that disappears means a test stopped declaring the runtime, and a line that
# appears means coverage moved out of the dependency-absent lane. Read the diff.
#
# The dependency-ABSENT lane requires this set to be skipped EXACTLY -- no missing entry,
# no extra one. The dependency-PRESENT lane requires every one of them to have EXECUTED.
"""


def load_expected_langgraph_skips(path: Path | None = None) -> frozenset[str]:
    """The declared identity set. Blank lines and `#` comments are ignored."""
    source = path or LANGGRAPH_SKIP_MANIFEST
    ids = set()
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ids.add(line)
    return frozenset(ids)


def pinned_langgraph_version() -> str:
    """The pinned version, read from the requirements file rather than transcribed."""
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("langgraph=="):
            return line.split("==", 1)[1].strip()
    raise RuntimeError(f"{REQUIREMENTS} pins no langgraph version")


def langgraph_available() -> bool:
    """The ONE reading of the runtime condition the lanes are named after.

    Deliberately the same three checks every ``_langgraph_ok`` in the test suite makes --
    importable, the graph subpackage importable, and the pinned version exactly -- so a
    lane assertion and a per-test gate can never disagree about what "present" means.
    """
    import importlib.metadata

    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == pinned_langgraph_version()
    except importlib.metadata.PackageNotFoundError:
        return False


def declared_lane() -> str | None:
    """The lane this process was told it is, or None when nobody declared one."""
    value = os.environ.get(LANE_ENV, "").strip().lower()
    return value if value in LANES else None


def is_langgraph_skip(reason: str) -> bool:
    return any(pattern.search(reason) for pattern in LANGGRAPH_SKIP_PATTERNS)


def tolerated_pairs(result: unittest.TestResult) -> set[tuple[str, str]]:
    """The (test id, reason) pairs this run produced for non-LangGraph skips."""
    return {(test.id(), reason) for test, reason in result.skipped
            if not is_langgraph_skip(reason)}


# ---- the lane condition ---------------------------------------------------------------


def check_lane_condition(lane: str) -> list[str]:
    """Assert the environment really is the lane it claims to be.

    Returns the problems found, so the caller reports all of them at once rather than
    one per re-run.
    """
    problems: list[str] = []
    available = langgraph_available()
    if lane == LANE_PRESENT:
        if not available:
            problems.append(
                f"lane 'present' requires langgraph=={pinned_langgraph_version()} but it "
                "is not importable at that version; the `pip install -r "
                "requirements-langgraph.txt` step did not take effect")
        else:
            problems.extend(_check_present_engine())
    else:
        if available:
            problems.append(
                "lane 'absent' requires langgraph to be UNINSTALLED, but it imports; this "
                "job would silently re-test the 'present' lane")
        else:
            problems.extend(_check_absent_degradation())
    return problems


def _check_present_engine() -> list[str]:
    """Importable is not enough -- the engine the lane exists to exercise must build."""
    problems: list[str] = []
    try:
        from scripts.deterministic_workflow.graph import build_graph  # noqa: F401
    except Exception as exc:  # pragma: no cover - reported, never swallowed
        problems.append(f"lane 'present' cannot import the compiled graph: {exc!r}")
    try:
        from scripts.deterministic_workflow import checkpoint_store  # noqa: F401
    except Exception as exc:  # pragma: no cover - reported, never swallowed
        problems.append(f"lane 'present' cannot import the durable checkpointer: {exc!r}")
    return problems


def _check_absent_degradation() -> list[str]:
    """The absent lane PASSES INTENTIONALLY: it asserts the fail-closed behaviour.

    Each item is a documented degraded guarantee, not an absence of one. The lane is
    green because these hold, never because the tests that would have checked them were
    skipped.
    """
    problems: list[str] = []

    # (i) The engine refuses by NAME rather than crashing with an import traceback.
    try:
        from scripts.deterministic_workflow.launcher import require_runtime

        require_runtime()
    except Exception as exc:
        if "LANGGRAPH_DEPENDENCY_MISSING" not in str(exc):
            problems.append(
                "lane 'absent': require_runtime() refused without naming "
                f"LANGGRAPH_DEPENDENCY_MISSING: {exc!r}")
    else:
        problems.append("lane 'absent': require_runtime() did not refuse")

    # (ii) The durable checkpointer is unimportable -- it is the LangGraph-bound half --
    #      and that must be an ImportError, not a partially-initialised module.
    try:
        from scripts.deterministic_workflow import checkpoint_store  # noqa: F401
    except ImportError:
        pass
    else:
        problems.append(
            "lane 'absent': checkpoint_store imported without langgraph")

    # (iii) The LangGraph-free half still WORKS. Without this the lane would pass just as
    #       well against a package that fails to import at all.
    try:
        from scripts.deterministic_workflow import pause_policy, pause_store

        if pause_policy.transition("ACTIVE", "ENTER_PAUSE") != "WAITING_FOR_INPUT":
            problems.append("lane 'absent': pause_policy degraded")
        if not pause_store.PAUSE_RECORD_SCHEMA_VERSION:
            problems.append("lane 'absent': pause_store degraded")
        from scripts.deterministic_workflow.audit_wrapper import _audited  # noqa: F401
    except Exception as exc:
        problems.append(
            f"lane 'absent': the LangGraph-free engine modules do not work: {exc!r}")
    return problems


# ---- the suite, with the lane's skip budget --------------------------------------------


class LaneResult(unittest.TextTestResult):
    """A result that remembers which test ids ran.

    `unittest` records failures, errors and skips but not passes, and the present lane has
    to assert something about tests that PASSED -- namely that every test the manifest says
    needs LangGraph actually executed there. Without this, "no langgraph skips" could also
    be satisfied by those tests having disappeared.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.started_ids: set[str] = set()

    def startTest(self, test: unittest.TestCase) -> None:
        self.started_ids.add(test.id())
        super().startTest(test)


def skipped_ids(result: unittest.TestResult, predicate) -> set[str]:
    return {test.id() for test, reason in result.skipped if predicate(reason)}


def run_suite(lane: str, *, verbosity: int = 1) -> tuple[unittest.TestResult, list[str]]:
    """Run exactly what CI's `unittest discover -s scripts -p 'test_*.py'` runs.

    Same loader, same start directory, same pattern -- so this is the suite, not a subset
    chosen to make a lane look better.
    """
    # `top_level_dir` is deliberately left at its default so this is byte-for-byte the
    # discovery `python3 -m unittest discover -s scripts -p 'test_*.py'` performs.
    # `scripts/` is a namespace package, and pinning a top level dir makes discovery
    # refuse to import it at all.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir="scripts", pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=verbosity, stream=sys.stderr,
                                     resultclass=LaneResult)
    result = runner.run(suite)
    return result, check_skip_budget(lane, result)


def check_skip_budget(lane: str, result: unittest.TestResult,
                      *, expected: frozenset[str] | None = None,
                      tolerated: frozenset[tuple[str, str]] | None = None) -> list[str]:
    """Hold the run against the lane's DECLARED expected condition, by identity.

    The earlier version of this function only rejected the zero case for the absent lane
    -- "something was skipped for a langgraph reason". That reproduced, one level up, the
    exact defect the external review raised: 246 skips passed, and so would 500, or the
    whole OS-42 graph/repair/crash/replay surface, as long as each reason said
    "langgraph". "Some tests were skipped" is not an assertion about which tests ran.

    So the contract is an IDENTITY SET, not a count. `LANGGRAPH_SKIP_MANIFEST` names every
    test expected to skip for a LangGraph reason in the dependency-absent lane, and both
    lanes are checked against it in the direction that can actually catch a regression:

    * absent lane -- the skipped set must EQUAL the manifest. A MISSING entry means a test
      that used to declare the runtime no longer does; an EXCESS entry means coverage
      quietly left this lane. Both fail.
    * present lane -- nothing may skip for a LangGraph reason, AND every test in the
      manifest must have EXECUTED. The second half is what stops "no langgraph skips" from
      being satisfied by those tests having been deleted or renamed away.

    A count contract was rejected: a bare number drifts silently. Delete one gated test,
    add another, and the total is unchanged while the covered surface is not.
    """
    problems: list[str] = []
    if expected is None:
        expected = load_expected_langgraph_skips()
    if tolerated is None:
        tolerated = load_tolerated_skips()

    langgraph_skipped = skipped_ids(result, is_langgraph_skip)

    if lane == LANE_PRESENT:
        if langgraph_skipped:
            problems.append(
                f"lane 'present' skipped {len(langgraph_skipped)} test(s) for a langgraph "
                "reason, so the pinned runtime is not actually installed: "
                + _sample(sorted(langgraph_skipped)))
        started = getattr(result, "started_ids", None)
        if started is None:
            problems.append(
                "lane 'present' cannot verify that the manifest's tests executed: the run "
                "produced no started-id record (expected a LaneResult)")
        else:
            never_ran = expected - started
            if never_ran:
                problems.append(
                    f"lane 'present' never ran {len(never_ran)} test(s) the LangGraph "
                    "manifest declares; they were deleted, renamed, or skipped for some "
                    "other reason, so this lane is no longer covering them: "
                    + _sample(sorted(never_ran))
                    + f" -- reconcile {LANGGRAPH_SKIP_MANIFEST.name}")
    else:
        missing = expected - langgraph_skipped
        excess = langgraph_skipped - expected
        if missing:
            problems.append(
                f"lane 'absent' did NOT skip {len(missing)} test(s) the LangGraph manifest "
                "declares. Either they lost their runtime gate, or they were renamed or "
                "deleted: " + _sample(sorted(missing))
                + f" -- reconcile {LANGGRAPH_SKIP_MANIFEST.name}")
        if excess:
            problems.append(
                f"lane 'absent' skipped {len(excess)} test(s) for a langgraph reason that "
                "the manifest does not declare. This is the masquerade the review named: "
                "coverage left the dependency-absent lane without anyone deciding it "
                "should: " + _sample(sorted(excess))
                + f" -- if that is intended, add them to {LANGGRAPH_SKIP_MANIFEST.name}")

    problems.extend(check_tolerated_skips(lane, result, tolerated))

    if result.failures or result.errors:
        problems.append(
            f"lane {lane!r} must pass: {len(result.failures)} failure(s), "
            f"{len(result.errors)} error(s)")
    return problems


def check_tolerated_skips(lane: str, result: unittest.TestResult,
                          tolerated: frozenset[tuple[str, str]]) -> list[str]:
    """The non-LangGraph half of the contract, by identity AND exact reason.

    Symmetric with the LangGraph manifest check, and for the same reason: a tolerated skip
    that is matched by wording alone is not tolerated, it is unbounded. Both directions
    fail, and a test that skips for NEW wording is reported as a reason mismatch rather
    than as an unrelated missing/excess pair, because that is what a maintainer needs to
    read.
    """
    problems: list[str] = []
    actual = tolerated_pairs(result)
    missing = tolerated - actual
    excess = actual - tolerated

    # A test present on both sides with different wording is one event, not two.
    missing_by_id = {test_id: reason for test_id, reason in missing}
    excess_by_id = {test_id: reason for test_id, reason in excess}
    reworded = sorted(set(missing_by_id) & set(excess_by_id))
    for test_id in reworded:
        problems.append(
            f"lane {lane!r}: tolerated skip {test_id} changed its reason from "
            f"{missing_by_id[test_id]!r} to {excess_by_id[test_id]!r}. A reworded reason "
            f"is a new skip; reconcile {TOLERATED_SKIP_MANIFEST.name}")

    still_missing = sorted(f"{i} ({r!r})" for i, r in missing if i not in set(reworded))
    still_excess = sorted(f"{i} ({r!r})" for i, r in excess if i not in set(reworded))

    if still_missing:
        problems.append(
            f"lane {lane!r} did NOT produce {len(still_missing)} declared non-LangGraph "
            "skip(s). Either the test now runs -- in which case remove it -- or it was "
            "renamed or deleted: " + _sample(still_missing)
            + f" -- reconcile {TOLERATED_SKIP_MANIFEST.name}")
    if still_excess:
        problems.append(
            f"lane {lane!r} produced {len(still_excess)} skip(s) that "
            f"{TOLERATED_SKIP_MANIFEST.name} does not declare. A skip is tolerated by "
            "IDENTITY, never by wording, so an additional test carrying an already-declared "
            "reason is still a failure: " + _sample(still_excess)
            + " -- fix the test, or declare it with a note saying why it may skip")
    return problems


def _sample(ids: list[str], limit: int = 5) -> str:
    shown = ", ".join(ids[:limit])
    return shown + (f", ... (+{len(ids) - limit} more)" if len(ids) > limit else "")


def summarize(lane: str, result: unittest.TestResult,
              *, expected: frozenset[str] | None = None,
              tolerated: frozenset[tuple[str, str]] | None = None) -> str:
    """The line a human reads in CI output. It reports the IDENTITY check, not just counts.

    `manifest=` / `matched=` / `missing=` / `unexpected=` are the whole point: a reader can
    see that the expected LangGraph-skipped set was declared and that the run matched it,
    rather than having to trust a skip total.
    """
    if expected is None:
        expected = load_expected_langgraph_skips()
    if tolerated is None:
        tolerated = load_tolerated_skips()
    langgraph_skipped = skipped_ids(result, is_langgraph_skip)
    executed = result.testsRun - len(result.skipped)
    if lane == LANE_PRESENT:
        started = getattr(result, "started_ids", set())
        matched = len(expected & started)
        missing = len(expected - started)
        unexpected = len(langgraph_skipped)
        scope = "manifest tests EXECUTED"
    else:
        matched = len(expected & langgraph_skipped)
        missing = len(expected - langgraph_skipped)
        unexpected = len(langgraph_skipped - expected)
        scope = "manifest tests SKIPPED"
    actual_tolerated = tolerated_pairs(result)
    return (
        f"lane={lane} collected={result.testsRun} executed={executed} "
        f"failures={len(result.failures)} errors={len(result.errors)} "
        f"skipped={len(result.skipped)} (langgraph={len(langgraph_skipped)}, "
        f"other={len(actual_tolerated)}) | "
        f"{scope}: manifest={len(expected)} matched={matched} missing={missing} "
        f"unexpected={unexpected} | "
        f"tolerated skips: manifest={len(tolerated)} "
        f"matched={len(tolerated & actual_tolerated)} "
        f"missing={len(tolerated - actual_tolerated)} "
        f"unexpected={len(actual_tolerated - tolerated)}")


def write_manifest(lane: str, *, verbosity: int = 0) -> int:
    """Regenerate LANGGRAPH_SKIP_MANIFEST from a real dependency-absent run.

    Refuses to run in the present lane: there is nothing to record there, and writing an
    empty manifest would silently disarm every assertion that depends on it.
    """
    if lane != LANE_ABSENT:
        print("CI_LANE_ERROR: write-manifest requires --lane absent; the manifest is the "
              "set of tests that skip when LangGraph is NOT installed", file=sys.stderr)
        return 1
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir="scripts", pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=verbosity, stream=sys.stderr,
                                     resultclass=LaneResult)
    result = runner.run(suite)
    ids = sorted(skipped_ids(result, is_langgraph_skip))
    if not ids:
        print("CI_LANE_ERROR: nothing skipped for a langgraph reason; refusing to write "
              "an empty manifest", file=sys.stderr)
        return 1
    LANGGRAPH_SKIP_MANIFEST.write_text(MANIFEST_HEADER + "\n".join(ids) + "\n",
                                       encoding="utf-8")
    print(f"wrote {len(ids)} test ids to {LANGGRAPH_SKIP_MANIFEST}")

    # The other half of the contract, written from the same run so the two cannot be
    # generated against different trees. An EMPTY tolerated set is legitimate -- it means
    # nothing outside the LangGraph gates skips -- so, unlike the manifest above, it is
    # written rather than refused.
    pairs = sorted(tolerated_pairs(result))
    TOLERATED_SKIP_MANIFEST.write_text(
        TOLERATED_MANIFEST_HEADER
        + "".join(f"{test_id}\t{reason}\n" for test_id, reason in pairs),
        encoding="utf-8")
    print(f"wrote {len(pairs)} tolerated skips to {TOLERATED_SKIP_MANIFEST}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("assert", "run", "write-manifest"))
    parser.add_argument("--lane", choices=LANES, required=True)
    parser.add_argument("--verbosity", type=int, default=1)
    args = parser.parse_args(argv)

    problems = check_lane_condition(args.lane)
    if problems:
        for problem in problems:
            print(f"CI_LANE_ERROR: {problem}", file=sys.stderr)
        return 1
    print(f"CI_LANE_OK: environment matches lane {args.lane!r} "
          f"(langgraph {'present' if langgraph_available() else 'absent'})")
    if args.command == "assert":
        return 0

    if args.command == "write-manifest":
        return write_manifest(args.lane, verbosity=args.verbosity)

    result, problems = run_suite(args.lane, verbosity=args.verbosity)
    print(summarize(args.lane, result))
    for problem in problems:
        print(f"CI_LANE_ERROR: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
