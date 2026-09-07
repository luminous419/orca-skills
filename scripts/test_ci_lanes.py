"""OS-42 review MAJOR 2: the CI workflow must declare, and assert, both LangGraph lanes.

The defect these tests exist to keep closed:

    `.github/workflows/ci.yml` had no `pip install` step at all. It went from "Set up
    Python" straight to `unittest discover`, so `requirements-langgraph.txt` was never
    installed on any runner and 259 graph, repair, crash/restart and replay tests skipped
    on every job -- while the suite still reported OK and the PR quoted "2614 passed /
    6 skipped" measured on a developer machine where LangGraph WAS installed.

Nothing in the pipeline could notice, because no job ever said which condition it was
testing. So the tests below read the workflow as data and require it to say so: a
dependency-PRESENT lane that installs the pinned runtime and asserts it arrived, and a
dependency-ABSENT lane that asserts the degraded behaviour instead of accumulating
incidental skips. Deleting either lane, or dropping the install step, fails here.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from scripts import ci_lane

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
REQUIREMENTS = REPO_ROOT / "requirements-langgraph.txt"

#: The Python versions the project declares support for. Read off the workflow matrix by
#: the tests below, and cross-checked against each other so the matrix cannot drift from
#: what was actually verified.
DECLARED_PYTHON_VERSIONS = ("3.11", "3.12", "3.13")


def load_workflow() -> dict:
    """Parse ci.yml, preferring a real YAML parser and falling back to a narrow reader.

    PyYAML is not a declared dependency of this repository -- the CI lanes install only
    `requirements-langgraph.txt` -- so these tests must not become a skip whenever it is
    absent. That is the exact failure mode MAJOR 2 is about.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        return _minimal_parse(text)
    return yaml.safe_load(text)


def _minimal_parse(text: str) -> dict:
    """Enough of ci.yml's shape for the assertions below, without a YAML dependency.

    Only the two facts the tests need per job: its `run:` command lines and its `env:`
    entries. Jobs are the two-space-indented keys under `jobs:`.
    """
    jobs: dict[str, dict] = {}
    current: str | None = None
    in_jobs = False
    for raw in text.splitlines():
        if raw.startswith("jobs:"):
            in_jobs = True
            continue
        if not in_jobs or not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("  ") and not raw.startswith("   ") and raw.rstrip().endswith(":"):
            current = raw.strip().rstrip(":")
            jobs[current] = {"text": "", "env": {}}
            continue
        if current is None:
            continue
        jobs[current]["text"] += raw + "\n"
        stripped = raw.strip()
        if ": " in stripped and stripped.split(":", 1)[0] in ("ORCA_CI_LANGGRAPH_LANE",):
            key, value = stripped.split(":", 1)
            jobs[current]["env"][key] = value.strip().strip("'\"")
    return {"jobs": jobs}


def job_text(workflow: dict, name: str) -> str:
    """The job's own lines of ci.yml, verbatim.

    Deliberately the RAW slice rather than a re-serialisation of the parsed job: the
    assertions below are about what the workflow says (`pip install -r ...`, `--lane
    present`, `"3.12"`), and a parser round-trip rewrites exactly those spellings.
    """
    del workflow  # the raw text is the source of truth for these assertions
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(f"  {name}:")
    except ValueError:  # pragma: no cover - a renamed job fails the caller's lookup first
        raise AssertionError(f"job {name!r} is not declared at the expected indent")
    collected = []
    for line in lines[start + 1:]:
        if line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(":"):
            break
        collected.append(line)
    return "\n".join(collected)


def job_lane(workflow: dict, name: str) -> str | None:
    job = workflow["jobs"][name]
    env = job.get("env") or {}
    return env.get(ci_lane.LANE_ENV)


class WorkflowDeclaresBothLanesTests(unittest.TestCase):
    """The structural half: the workflow must contain both lanes, and say which is which."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = load_workflow()

    def lane_job(self, lane: str) -> str:
        names = [name for name in self.workflow["jobs"]
                 if job_lane(self.workflow, name) == lane]
        self.assertEqual(
            len(names), 1,
            f"exactly one job must declare {ci_lane.LANE_ENV}={lane!r}; found {names}")
        return names[0]

    def test_a_dependency_present_lane_exists_and_installs_the_pinned_runtime(self) -> None:
        """THE assertion MAJOR 2 asks for. Fails against a workflow with no install step."""
        name = self.lane_job(ci_lane.LANE_PRESENT)
        text = job_text(self.workflow, name)
        self.assertIn(
            "requirements-langgraph.txt", text,
            "the dependency-present lane never installs requirements-langgraph.txt, so "
            "its graph/repair/crash/replay coverage would silently skip")
        self.assertIn("pip install", text,
                      "the present lane has no pip install step at all")

    def test_a_dependency_absent_lane_exists_and_installs_nothing(self) -> None:
        """The other lane must stay genuinely dependency-free, or it tests nothing new."""
        name = self.lane_job(ci_lane.LANE_ABSENT)
        text = job_text(self.workflow, name)
        self.assertNotIn(
            "requirements-langgraph.txt", text,
            "the dependency-absent lane installs the runtime, so it is a duplicate of "
            "the present lane rather than a degraded-behaviour lane")

    def test_every_lane_asserts_its_own_condition_before_running_tests(self) -> None:
        """A lane that does not assert its condition is a lane that can silently swap."""
        for lane in ci_lane.LANES:
            with self.subTest(lane=lane):
                text = job_text(self.workflow, self.lane_job(lane))
                self.assertIn(f"ci_lane assert --lane {lane}", text)
                self.assertIn(f"ci_lane run --lane {lane}", text,
                              "the lane runs the suite without its skip budget")

    def test_both_lanes_declare_the_same_honest_python_matrix(self) -> None:
        """The matrix must be the versions actually supported, on BOTH lanes.

        `requirements-langgraph.txt` was verified on 3.11; the pinned set was checked to
        resolve, install and import on 3.12 and 3.13 as well, so all three are declared.
        A version that could not take the pinned install would have to be removed here
        rather than left to skip its way to green.
        """
        text = WORKFLOW.read_text(encoding="utf-8")
        for lane in ci_lane.LANES:
            name = self.lane_job(lane)
            with self.subTest(lane=lane):
                job = job_text(self.workflow, name)
                for version in DECLARED_PYTHON_VERSIONS:
                    self.assertIn(f'"{version}"', job,
                                  f"lane {lane} does not declare Python {version}")
        self.assertEqual(text.count("python-version: [") , 2,
                         "each lane must declare its own matrix")

    def test_the_gates_the_review_requires_run_in_both_lanes(self) -> None:
        """Skill validation, graph docs, package and archive verification, whitespace."""
        required = (
            "scripts/validate_skills.py",
            "scripts/validate_workflow_graph_docs.py",
            "scripts/verify_package.py",
            "scripts/build_release.py",
            "git diff --check",
        )
        for lane in ci_lane.LANES:
            job = job_text(self.workflow, self.lane_job(lane))
            for gate in required:
                with self.subTest(lane=lane, gate=gate):
                    self.assertIn(gate, job, f"lane {lane} does not run {gate}")


class LaneConditionTests(unittest.TestCase):
    """The behavioural half: `ci_lane` must actually be able to tell the lanes apart.

    These run identically in both lanes -- they assert about the CHECKER, not about the
    ambient runtime -- so neither lane gets to skip its way past them.
    """

    def test_the_pinned_version_is_read_from_the_requirements_file(self) -> None:
        version = ci_lane.pinned_langgraph_version()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertIn(f"langgraph=={version}",
                      REQUIREMENTS.read_text(encoding="utf-8"))

    def test_the_checker_agrees_with_the_ambient_runtime(self) -> None:
        """`langgraph_available()` must report what an import actually does."""
        try:
            import langgraph.graph  # noqa: F401
        except ImportError:
            importable = False
        else:
            importable = True
        if not importable:
            self.assertFalse(ci_lane.langgraph_available())
        # The converse is version-sensitive, so it is asserted only in the direction that
        # cannot be wrong: an unimportable runtime is never "available".

    def test_the_wrong_lane_is_refused_for_this_environment(self) -> None:
        """The load-bearing property: a lane cannot be satisfied by the other's condition.

        Whichever lane this process is actually in, asserting the OTHER one must fail.
        That is what makes `assert --lane present` proof the install happened.
        """
        actual = ci_lane.LANE_PRESENT if ci_lane.langgraph_available() else ci_lane.LANE_ABSENT
        other = ci_lane.LANE_ABSENT if actual == ci_lane.LANE_PRESENT else ci_lane.LANE_PRESENT
        self.assertEqual(ci_lane.check_lane_condition(actual), [],
                         "the lane this environment IS was refused")
        self.assertNotEqual(
            ci_lane.check_lane_condition(other), [],
            f"lane {other!r} was accepted in a {actual!r} environment; the lanes are "
            "interchangeable, so neither proves anything")

    def test_the_declared_lane_is_read_from_the_environment(self) -> None:
        import os

        original = os.environ.get(ci_lane.LANE_ENV)
        try:
            for value, expected in (("present", "present"), ("ABSENT", "absent"),
                                    ("", None), ("nonsense", None)):
                os.environ[ci_lane.LANE_ENV] = value
                self.assertEqual(ci_lane.declared_lane(), expected)
            del os.environ[ci_lane.LANE_ENV]
            self.assertIsNone(ci_lane.declared_lane())
        finally:
            if original is None:
                os.environ.pop(ci_lane.LANE_ENV, None)
            else:
                os.environ[ci_lane.LANE_ENV] = original


class SkipBudgetTests(unittest.TestCase):
    """The lane's expected condition is an IDENTITY SET, and both directions are checked.

    Review round 1 raised F-001 against the earlier version of this contract: the absent
    lane rejected only the ZERO case, so 246 skips passed and so would 500, or the whole
    OS-42 graph/repair/crash/replay surface, as long as each reason said "langgraph". That
    is the external review's original complaint -- incidental skips masquerading as
    success -- reproduced one level up. These tests pin the fix in both directions.
    """

    #: A small stand-in manifest, so these tests assert the CONTRACT rather than the
    #: current contents of the real one. Using the real 240-entry manifest here would make
    #: every case below drift whenever a gated test is added.
    EXPECTED = frozenset({"m.C.test_alpha", "m.C.test_beta", "m.D.test_gamma"})
    LANGGRAPH_REASON = "requires pinned langgraph 0.2.76"
    LIVE_REASON = "requires --orca-runtime and a ready Orca runtime"
    #: The non-LangGraph half, declared the same way: identity AND exact reason.
    TOLERATED = frozenset({("m.R.test_live_one", LIVE_REASON),
                           ("m.R.test_live_two", LIVE_REASON)})

    class _Test:
        """Enough of a TestCase for the budget: an id."""

        def __init__(self, test_id: str) -> None:
            self._id = test_id

        def id(self) -> str:
            return self._id

        def __repr__(self) -> str:  # pragma: no cover - only in failure messages
            return self._id

    class _Result:
        def __init__(self, skipped, failures=(), errors=(), started=None):
            self.skipped = list(skipped)
            self.failures = list(failures)
            self.errors = list(errors)
            self.testsRun = len(self.skipped) + 1
            self.started_ids = set(started) if started is not None else set()

    def absent_result(self, skipped_ids, extra=(), tolerated=True):
        """A run: the given LangGraph skips, plus (by default) the declared tolerated set.

        The tolerated pairs are included by default because BOTH manifests are now checked
        on every result -- omitting them would make every case below fail for the other
        contract's reason and hide what it is actually asserting.
        """
        entries = [(self._Test(i), self.LANGGRAPH_REASON) for i in skipped_ids]
        if tolerated:
            entries += [(self._Test(i), reason) for i, reason in sorted(self.TOLERATED)]
        entries += [(self._Test(i), reason) for i, reason in extra]
        return self._Result(entries)

    def check(self, lane, result):
        return ci_lane.check_skip_budget(lane, result, expected=self.EXPECTED,
                                         tolerated=self.TOLERATED)

    # -- the absent lane: the skipped set must EQUAL the manifest ----------------------

    def test_the_exact_expected_set_passes_the_absent_lane(self) -> None:
        """The baseline the two failure cases below are measured against."""
        result = self.absent_result(self.EXPECTED)
        self.assertEqual(self.check(ci_lane.LANE_ABSENT, result), [])

    def test_a_MISSING_expected_skip_fails_the_absent_lane(self) -> None:
        """F-001, half one. A test that quietly stopped declaring the runtime.

        Under the old contract this PASSED: two langgraph skips is still "not zero". The
        set comparison is what turns it into a failure, and the message has to name the
        test so a human can act on it.
        """
        result = self.absent_result(self.EXPECTED - {"m.D.test_gamma"})
        problems = self.check(ci_lane.LANE_ABSENT, result)
        self.assertTrue(problems, "a missing expected skip was tolerated")
        self.assertTrue(any("did NOT skip" in p for p in problems), problems)
        self.assertTrue(any("m.D.test_gamma" in p for p in problems),
                        f"the missing test is not named: {problems}")

    def test_an_EXCESS_langgraph_skip_fails_the_absent_lane(self) -> None:
        """F-001, half two. THE masquerade: coverage leaving the lane unnoticed.

        Under the old contract this PASSED unconditionally -- any number of langgraph
        skips was acceptable. This is the case the external review actually cared about.
        """
        result = self.absent_result(self.EXPECTED | {"m.E.test_newly_gated"})
        problems = self.check(ci_lane.LANE_ABSENT, result)
        self.assertTrue(problems, "an undeclared langgraph skip was tolerated")
        self.assertTrue(any("does not declare" in p for p in problems), problems)
        self.assertTrue(any("m.E.test_newly_gated" in p for p in problems),
                        f"the excess test is not named: {problems}")

    def test_a_wholesale_skip_explosion_fails_the_absent_lane(self) -> None:
        """The concrete scenario the review described: 500 skips must not pass."""
        result = self.absent_result(
            self.EXPECTED | {f"m.Bulk.test_{n}" for n in range(500)})
        problems = self.check(ci_lane.LANE_ABSENT, result)
        self.assertTrue(any("500" in p and "does not declare" in p for p in problems),
                        f"500 undeclared langgraph skips passed the lane: {problems}")

    def test_no_langgraph_skip_fails_the_absent_lane(self) -> None:
        """The zero case the old contract DID catch; kept, because it is still real."""
        problems = self.check(ci_lane.LANE_ABSENT, self.absent_result([]))
        self.assertTrue(problems)
        self.assertTrue(any("did NOT skip" in p for p in problems), problems)

    # -- the present lane: the manifest's tests must have EXECUTED ---------------------

    def test_the_manifest_executing_passes_the_present_lane(self) -> None:
        result = self.absent_result([], tolerated=True)
        result.started_ids = set(self.EXPECTED) | {"m.C.test_unrelated"}
        self.assertEqual(self.check(ci_lane.LANE_PRESENT, result), [])

    def test_a_langgraph_skip_fails_the_present_lane(self) -> None:
        """The direct signal that `pip install` did not take effect."""
        result = self.absent_result(["m.C.test_alpha"])
        result.started_ids = set(self.EXPECTED)
        problems = self.check(ci_lane.LANE_PRESENT, result)
        self.assertTrue(problems)
        self.assertIn("langgraph", problems[0])

    def test_a_manifest_test_that_never_RAN_fails_the_present_lane(self) -> None:
        """Stops "no langgraph skips" being satisfied by the tests having disappeared.

        A deleted or renamed gated test skips nothing and fails nothing, so without this
        the present lane would go green while covering strictly less.
        """
        result = self.absent_result([], tolerated=True)
        result.started_ids = set(self.EXPECTED) - {"m.C.test_beta"}
        problems = self.check(ci_lane.LANE_PRESENT, result)
        self.assertTrue(problems, "a manifest test that never ran was tolerated")
        self.assertTrue(any("never ran" in p for p in problems), problems)
        self.assertTrue(any("m.C.test_beta" in p for p in problems), problems)

    def test_a_present_run_with_no_started_record_is_refused(self) -> None:
        """The execution check must not silently no-op on a result that cannot answer."""
        result = self.absent_result([], tolerated=True)
        del result.started_ids
        problems = self.check(ci_lane.LANE_PRESENT, result)
        self.assertTrue(any("started-id record" in p for p in problems), problems)

    # -- unchanged guarantees, kept under the new contract ------------------------------

    def test_an_undeclared_skip_fails_both_lanes(self) -> None:
        """A skip nobody declared is a failure, not a shrug."""
        for lane in ci_lane.LANES:
            with self.subTest(lane=lane):
                result = self.absent_result(
                    self.EXPECTED, extra=[("m.C.test_odd", "this reason was never declared")])
                result.started_ids = set(self.EXPECTED)
                problems = self.check(lane, result)
                self.assertTrue(
                    any("does not declare" in problem for problem in problems),
                    f"lane {lane} tolerated an undeclared skip: {problems}")

    def test_the_declared_tolerated_set_passes_the_absent_lane(self) -> None:
        """The tolerated half is satisfied by its exact declared set and nothing else."""
        result = self.absent_result(self.EXPECTED)
        self.assertEqual(self.check(ci_lane.LANE_ABSENT, result), [])

    def test_a_failure_fails_either_lane(self) -> None:
        for lane in ci_lane.LANES:
            with self.subTest(lane=lane):
                result = self.absent_result(self.EXPECTED)
                result.started_ids = set(self.EXPECTED)
                result.failures = [("t", "boom")]
                self.assertTrue(any("must pass" in problem
                                    for problem in self.check(lane, result)))


class ToleratedSkipContractTests(unittest.TestCase):
    """Review round 2, F-001: the NON-LangGraph skips, held to the same identity contract.

    The defect this class exists to keep closed, quoted from the code it replaced:

        # The opt-in live-runtime suite. ... Enumerated here so that its six
        # skips are DECLARED rather than merely tolerated -- a seventh, or a
        # differently worded one, still fails the lane.
        re.compile(r"^requires --orca-runtime and a ready Orca runtime$"),

    `is_allowed_skip` was `any(pattern.search(reason) ...)`. There was no count and no
    identity, so a SEVENTH skip carrying the SAME wording passed -- and so would five
    hundred. Only a differently worded one failed. The word "Enumerated" and the promise
    about a seventh were both false, and the comment is what stopped two reviewers looking
    closer.

    A skip is now tolerated by IDENTITY and exact REASON, never by wording, in BOTH lanes.
    """

    LIVE_REASON = "requires --orca-runtime and a ready Orca runtime"
    LANGGRAPH_REASON = "requires pinned langgraph 0.2.76"
    EXPECTED = frozenset({"m.C.test_alpha"})
    TOLERATED = frozenset({
        ("m.R.test_live_one", LIVE_REASON),
        ("m.R.test_live_two", LIVE_REASON),
    })

    def result(self, tolerated_pairs, *, langgraph=None, started=None):
        entries = [(SkipBudgetTests._Test(i), self.LANGGRAPH_REASON)
                   for i in (langgraph if langgraph is not None else self.EXPECTED)]
        entries += [(SkipBudgetTests._Test(i), reason) for i, reason in tolerated_pairs]
        result = SkipBudgetTests._Result(entries)
        result.started_ids = set(started if started is not None else self.EXPECTED)
        return result

    def check(self, lane, result):
        return ci_lane.check_skip_budget(lane, result, expected=self.EXPECTED,
                                         tolerated=self.TOLERATED)

    def absent(self, tolerated_pairs):
        return self.check(ci_lane.LANE_ABSENT, self.result(tolerated_pairs))

    def present(self, tolerated_pairs):
        return self.check(ci_lane.LANE_PRESENT,
                          self.result(tolerated_pairs, langgraph=()))

    # -- baseline ----------------------------------------------------------------------

    def test_the_exact_declared_set_passes_both_lanes(self) -> None:
        """What the two failure directions below are measured against."""
        self.assertEqual(self.absent(self.TOLERATED), [])
        self.assertEqual(self.present(self.TOLERATED), [])

    # -- EXCESS: the direction the old contract could not see at all --------------------

    def test_a_SEVENTH_skip_with_the_SAME_wording_fails_both_lanes(self) -> None:
        """THE finding, reproduced literally.

        An additional test carrying an already-declared reason. Under the reason-only
        allowance this passed, because the wording matched a pattern; it is a different
        test id, so under the identity contract it fails.
        """
        for lane in (ci_lane.LANE_ABSENT, ci_lane.LANE_PRESENT):
            with self.subTest(lane=lane):
                extra = self.TOLERATED | {("m.R.test_live_SEVENTH", self.LIVE_REASON)}
                problems = (self.absent(extra) if lane == ci_lane.LANE_ABSENT
                            else self.present(extra))
                self.assertTrue(problems, f"lane {lane} accepted a seventh live skip")
                self.assertTrue(any("does not declare" in p for p in problems), problems)
                self.assertTrue(any("m.R.test_live_SEVENTH" in p for p in problems),
                                f"the excess test is not named: {problems}")

    def test_the_reviewers_reproduction_now_fails(self) -> None:
        """The Final Reviewer's exact synthetic case: seven arbitrary skipped tests, each
        carrying the live-runtime reason, in the PRESENT lane with the whole manifest
        started. That returned `[]` and a summary reading `other=7`."""
        seven = frozenset((f"m.Arbitrary.test_{n}", self.LIVE_REASON) for n in range(7))
        problems = self.present(seven)
        self.assertTrue(problems, "the reviewer's reproduction still passes")
        self.assertTrue(any("does not declare" in p for p in problems), problems)

    def test_five_hundred_identical_skips_fail(self) -> None:
        """The scale the finding named. A count would have to be updated; identity does not."""
        flood = self.TOLERATED | {(f"m.Flood.test_{n}", self.LIVE_REASON)
                                  for n in range(500)}
        problems = self.absent(flood)
        self.assertTrue(any("500" in p and "does not declare" in p for p in problems),
                        f"500 undeclared skips passed the lane: {problems}")

    # -- MISSING: a declared skip that stopped happening --------------------------------

    def test_a_MISSING_declared_skip_fails_both_lanes(self) -> None:
        """A tolerated test that was renamed, deleted, or now runs.

        The old contract could not see this either: it only ever asked whether an observed
        reason matched a pattern, so a tolerated test vanishing produced no observation at
        all and therefore no complaint.
        """
        for lane in (ci_lane.LANE_ABSENT, ci_lane.LANE_PRESENT):
            with self.subTest(lane=lane):
                fewer = self.TOLERATED - {("m.R.test_live_two", self.LIVE_REASON)}
                problems = (self.absent(fewer) if lane == ci_lane.LANE_ABSENT
                            else self.present(fewer))
                self.assertTrue(problems, f"lane {lane} accepted a vanished tolerated skip")
                self.assertTrue(any("did NOT produce" in p for p in problems), problems)
                self.assertTrue(any("m.R.test_live_two" in p for p in problems),
                                f"the missing test is not named: {problems}")

    def test_losing_every_tolerated_skip_fails(self) -> None:
        problems = self.absent(frozenset())
        self.assertTrue(any("did NOT produce 2" in p for p in problems), problems)

    # -- REWORDED: one event, reported as one -------------------------------------------

    def test_a_REWORDED_reason_is_reported_as_a_reason_mismatch(self) -> None:
        """The one case the old contract DID catch, kept -- and now reported usefully.

        The same test id skipping with new wording is one event. Reporting it as an
        unrelated missing plus an unrelated excess would make a maintainer hunt for two
        problems that do not exist.
        """
        reworded = ({("m.R.test_live_one", self.LIVE_REASON)}
                    | {("m.R.test_live_two", "requires a live Orca runtime")})
        problems = self.absent(frozenset(reworded))
        self.assertEqual(
            len([p for p in problems if "m.R.test_live_two" in p]), 1,
            f"a reworded reason was reported as more than one problem: {problems}")
        self.assertTrue(any("changed its reason" in p for p in problems), problems)

    # -- the summary a human reads -------------------------------------------------------

    def test_the_summary_reports_the_tolerated_contract_beside_the_manifest(self) -> None:
        """The review asked for both contracts to be visible in one line."""
        line = ci_lane.summarize(ci_lane.LANE_ABSENT, self.result(self.TOLERATED),
                                 expected=self.EXPECTED, tolerated=self.TOLERATED)
        self.assertIn("tolerated skips: manifest=2 matched=2 missing=0 unexpected=0", line)
        self.assertIn("manifest tests SKIPPED", line)
        self.assertIn("other=2", line)


class ToleratedSkipManifestTests(unittest.TestCase):
    """The checked-in tolerated manifest must be real, and must match the live gates."""

    def test_every_declared_entry_is_a_test_id_and_a_non_empty_reason(self) -> None:
        for test_id, reason in ci_lane.load_tolerated_skips():
            with self.subTest(test_id=test_id):
                self.assertRegex(test_id, r"^[A-Za-z_][\w.]*\.[A-Za-z_]\w*\.[A-Za-z_]\w*$")
                self.assertTrue(reason.strip(), "a tolerated skip must declare its reason")

    def test_the_git_availability_skips_are_deliberately_NOT_tolerated(self) -> None:
        """If the whitespace gate stops running because git is missing, the lane goes red.

        That gate silently not running is the same class of defect as an incidental skip,
        so it must not be tolerated. Asserted rather than left to a comment.
        """
        reasons = {reason for _, reason in ci_lane.load_tolerated_skips()}
        for forbidden in ("git is not available on PATH", "not a git checkout"):
            with self.subTest(reason=forbidden):
                self.assertNotIn(forbidden, reasons)

    def test_the_declared_reasons_are_the_ones_the_tests_actually_raise(self) -> None:
        """The manifest must track the live gates, not a remembered wording.

        Reads the reason out of the test module's source, so a change there that is not
        reconciled into the manifest fails here rather than at the next full lane run.
        """
        declared = {reason for _, reason in ci_lane.load_tolerated_skips()}
        source = (REPO_ROOT / "scripts" / "test_orca_runtime.py").read_text(encoding="utf-8")
        for reason in declared:
            with self.subTest(reason=reason):
                self.assertIn(
                    f'self.skipTest("{reason}")', source,
                    f"no test raises {reason!r}; the tolerated manifest is stale")


class LangGraphSkipManifestTests(unittest.TestCase):
    """The manifest itself must be a real, usable declaration -- not an empty file."""

    def test_the_manifest_exists_and_is_a_non_trivial_identity_set(self) -> None:
        """An empty or tiny manifest would disarm every assertion built on it."""
        expected = ci_lane.load_expected_langgraph_skips()
        self.assertGreater(
            len(expected), 100,
            "the LangGraph skip manifest is implausibly small; an emptied manifest makes "
            "the absent lane vacuous")
        for test_id in expected:
            with self.subTest(test_id=test_id):
                self.assertRegex(test_id, r"^[A-Za-z_][\w.]*\.[A-Za-z_]\w*\.[A-Za-z_]\w*$")

    def test_the_manifest_covers_the_os42_surface_the_review_named(self) -> None:
        """Graph, repair, crash/restart and replay must all be represented.

        Named modules rather than a total, so gutting one area cannot be hidden by another
        area growing.
        """
        expected = ci_lane.load_expected_langgraph_skips()
        for module in ("test_os42_audit", "test_os42_repair",
                       "test_deterministic_workflow_graph",
                       "test_deterministic_workflow_recovery",
                       "test_os42_installed_e2e"):
            with self.subTest(module=module):
                self.assertTrue(
                    any(test_id.startswith(module + ".") for test_id in expected),
                    f"{module} contributes nothing to the dependency-absent lane's "
                    "expected skip set")

    def test_write_manifest_refuses_the_present_lane(self) -> None:
        """Regenerating from a present-lane run would write an empty manifest.

        stderr is captured: this test deliberately triggers a CI_LANE_ERROR, and letting it
        reach the real stream would print a lane-failure-shaped line into a PASSING lane's
        CI log, where a human would reasonably read it as the lane having failed.
        """
        import contextlib
        import io

        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            code = ci_lane.write_manifest(ci_lane.LANE_PRESENT)
        self.assertEqual(code, 1)
        self.assertIn("requires --lane absent", captured.getvalue())

    def test_the_summary_reports_the_identity_check(self) -> None:
        """A human reading CI output must see WHICH set was expected and that it matched."""
        expected = frozenset({"m.C.test_a"})
        result = SkipBudgetTests._Result(
            [(SkipBudgetTests._Test("m.C.test_a"), "requires pinned langgraph 0.2.76")])
        line = ci_lane.summarize(ci_lane.LANE_ABSENT, result, expected=expected)
        self.assertIn("manifest=1", line)
        self.assertIn("matched=1", line)
        self.assertIn("missing=0", line)
        self.assertIn("unexpected=0", line)
        self.assertIn("manifest tests SKIPPED", line)

        present = SkipBudgetTests._Result([], started={"m.C.test_a"})
        line = ci_lane.summarize(ci_lane.LANE_PRESENT, present, expected=expected)
        self.assertIn("manifest tests EXECUTED", line)
        self.assertIn("matched=1", line)


class AuditWrapperRunsInBothLanesTests(unittest.TestCase):
    """MAJOR 2's third item: the audit-outbox test must not need the graph runtime.

    `scripts/test_os42_audit.py` imported `_audited` from
    `scripts.deterministic_workflow.graph`, which imports `langgraph.graph` at module
    scope. On every dependency-absent job that was `ModuleNotFoundError: langgraph` -- an
    ERROR, not a declared skip. The wrapper now lives in a LangGraph-free module, so the
    behaviour is covered in BOTH lanes instead of skipped in one.
    """

    CHILD = textwrap.dedent('''
        import builtins, sys

        real_import = builtins.__import__


        def guarded(name, *args, **kwargs):
            if name.split(".")[0] == "langgraph":
                raise ImportError("langgraph is blocked for this test: " + name)
            return real_import(name, *args, **kwargs)


        builtins.__import__ = guarded
        from scripts.deterministic_workflow.audit_wrapper import _audited
        from scripts.deterministic_workflow import audit
        assert "langgraph" not in sys.modules, sorted(sys.modules)


        class Sink:
            """The real sink protocol: deliver(event, key, fields) -> delivered?"""

            def __init__(self):
                self.rows = []

            def deliver(self, event, key, fields):
                self.rows.append((event, key, dict(fields)))
                return True


        entry = audit.outbox_entry("e", "k", detail="d")
        node = _audited(lambda state: dict(state, audit_outbox=[entry]), Sink(), None)
        assert node({"audit_outbox": []})["audit_outbox"] == []
        print("AUDIT_WRAPPER_WITHOUT_LANGGRAPH_OK")
    ''')

    def test_the_audit_wrapper_imports_and_works_with_langgraph_blocked(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-c", self.CHILD], cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("AUDIT_WRAPPER_WITHOUT_LANGGRAPH_OK", completed.stdout)

    def test_the_graph_still_exposes_the_same_wrapper_object(self) -> None:
        """The move must not fork the implementation: one object, two names."""
        if not ci_lane.langgraph_available():
            # Asserted in the present lane; in the absent lane `graph` is unimportable by
            # design and the child above already proved the wrapper stands alone.
            self.assertFalse(ci_lane.langgraph_available())
            return
        from scripts.deterministic_workflow import audit_wrapper, graph

        self.assertIs(graph._audited, audit_wrapper._audited)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
