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
import ast
import json
import os
import re
import subprocess
import sys
import unittest
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

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
# `<condition><TAB><test id><TAB><exact skip reason>`.
#
# Generated by `python3 -m scripts.ci_lane write-manifest --lane absent`. Both lanes
# require the set expected UNDER THIS ENVIRONMENT to match EXACTLY: a missing entry, an
# extra one, or the same test skipping with different wording each fail the lane.
#
# CONDITIONS. A skip is never tolerated unconditionally; it is expected under a stated
# condition, evaluated against the real environment at check time:
#
#   always           expected on every platform (the opt-in live-runtime suite, gated on
#                    ORCA_RUNTIME_TEST=1, which no CI runner sets)
#   not_darwin       expected iff sys.platform != "darwin" (the seatbelt backend)
#   no_sandbox_exec  expected iff /usr/bin/sandbox-exec is absent
#
# A test may declare more than one condition; the lines are in PRECEDENCE order, matching
# how unittest resolves stacked gates (the outermost decorator's reason wins). On macOS the
# platform-gated tests resolve to NOTHING and must RUN -- if one of them skips, the lane
# fails. On Linux they must skip. Neither host gets the weaker contract.
#
# OS-37 adds twenty-one `always` entries.  All are opt-in live-runtime suites gated on an
# env var no CI runner sets: the live per-CLI checks need a real agent CLI installed, the
# standalone E2E spawns real local processes, and the R10 workflow E2E drives nine of them
# through the real `run_workflow.py` with `orca` removed from PATH.  AC-37-24 requires a check that cannot run to be
# recorded as "not established" rather than as a pass, which is what a declared skip is;
# artifacts/runs/run_54d90086bd75/CONFORMANCE.md and TEST.md record all of them.
#
# Deliberately absent, so they fail the lane if they ever occur: the git-availability skips
# in the retained-report whitespace gate. If that gate stops running because git is missing
# or the checkout is shallow, CI must go red, not quietly tolerate it.
"""


#: The EXACT set of tests that are expected to skip for a LangGraph reason in the
#: dependency-absent lane, one `unittest` test id per line. This is a checked-in
#: declaration, deliberately independent of what the run happens to produce -- comparing
#: the run against something derived from the same gates would be circular and would
#: assert nothing.
#:
#: Unlike the tolerated-skip manifest below, this one needs NO platform condition, and that
#: is a property rather than an accident: a LangGraph gate is a DEPENDENCY gate, identical
#: on every platform, and the two lanes are defined by it.
#: `LangGraphManifestIsPlatformIndependentTests` keeps it that way by asserting that no test
#: is gated on BOTH the runtime and the platform -- which is the one way this set could
#: start differing between macOS and the runner.
#:
#: Regenerate with `python3 -m scripts.ci_lane write-manifest --lane absent` from an
#: environment where LangGraph is genuinely absent, and read the diff before committing it:
#: a line DISAPPEARING means a test stopped needing the runtime (or was deleted), and a line
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
# No platform condition applies: a LangGraph gate is a dependency gate, and no test is
# gated on both the runtime and the platform (asserted by test_ci_lanes.py).
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


#: Where the seatbelt backend lives. Duplicated from `scripts/review_isolation.py` on
#: purpose: `ci_lane` must stay importable in the dependency-ABSENT lane, and importing the
#: isolation module to read one string would couple the lane checker to it. The duplication
#: is not allowed to rot -- `ToleratedSkipConditionTests` asserts the two agree.
SANDBOX_EXEC = "/usr/bin/sandbox-exec"

#: The conditions a declared skip may be expected UNDER. Each is a predicate over the real
#: environment, evaluated at check time, and NONE of them is "tolerated anywhere".
#:
#: This is what review round 3 added. The previous contract modelled *which* tests may skip
#: but not *under what condition*, so a manifest generated on macOS was exact on macOS and
#: wrong on ubuntu-latest: CI run 34092393153 failed all six jobs with 22 undeclared skips.
#: Adding those 22 unconditionally would have inverted the defect -- green on Linux, red on
#: a developer's Mac, where those same tests RUN -- which is the same environment-blindness
#: the external review raised in the first place.
CONDITIONS: dict[str, Callable[[], bool]] = {
    # The live-runtime suite: gated on ORCA_RUNTIME_TEST=1, which nothing in CI sets, so it
    # is expected to skip on every platform. "always" is a condition, not an exemption.
    "always": lambda: True,
    # The seatbelt backend is darwin-only; on every other platform T-8.9 carries the
    # fail-closed guarantee instead and these tests declare themselves skipped.
    "not_darwin": lambda: sys.platform != "darwin",
    # A darwin host without the binary, and every non-darwin host.
    "no_sandbox_exec": lambda: not Path(SANDBOX_EXEC).exists(),
}


def holding_conditions() -> tuple[str, ...]:
    """The condition tokens true in THIS environment, in manifest precedence order."""
    return tuple(name for name in CONDITIONS if CONDITIONS[name]())


def load_tolerated_alternatives(
    path: Path | None = None,
) -> dict[str, list[tuple[str, str]]]:
    """The declared skip conditions, as `{test id: [(condition, reason), ...]}`.

    The list is ORDERED and the order is precedence, mirroring how `unittest` resolves a
    test carrying more than one gate: the outermost decorator sets
    ``__unittest_skip_why__`` last and therefore wins. Thirteen of the isolation tests carry
    both ``@DARWIN_ONLY`` and ``@NEEDS_SANDBOX``; on Linux both conditions hold and the
    darwin reason is the one the runner records, so ``not_darwin`` is listed first for them.
    """
    source = path or TOLERATED_SKIP_MANIFEST
    alternatives: dict[str, list[tuple[str, str]]] = {}
    for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise ValueError(
                f"{source.name}:{number}: expected "
                f"'<condition><TAB><test id><TAB><reason>', got {line!r}")
        condition, test_id, reason = (part.strip() for part in parts)
        if condition not in CONDITIONS:
            raise ValueError(
                f"{source.name}:{number}: unknown condition {condition!r}; "
                f"expected one of {sorted(CONDITIONS)}")
        alternatives.setdefault(test_id, []).append((condition, reason))
    return alternatives


# ---- OS-37 external review #12: the manifest is bound to the DECLARED TEST -------------
# The anti-drift check used to accept "this module contains the reason string somewhere AND
# this module contains a `skipUnless(` or `skipIf(` somewhere". Both halves are module-wide,
# so an entry could name `Foo.test_bar` while the reason lived in an unrelated docstring and
# the decorator guarded an unrelated class -- manifest and gate drifted apart with the check
# still green. What follows resolves the guards that actually apply to ONE test id, so an
# entry can only pass by naming a test that really is guarded with that reason.
#
# It reads the source with `ast` rather than importing the module: importing would execute
# module-level gates (and, for the live suites, probe the host), and the question here is a
# static one about what the source declares.
_SKIP_CALLS = ("skipUnless", "skipIf")

#: One resolved guard: the text to show an operator, and the matcher it binds with.
SkipGuard = tuple[str, "re.Pattern[str]"]


def _exact(value: str) -> SkipGuard:
    return value, re.compile("^" + re.escape(value) + "$", re.DOTALL)


def _joined_str_guard(node: Any) -> SkipGuard | None:
    """An f-string reason as an anchored pattern, or ``None``.

    `test_review_isolation`'s ``NEEDS_SANDBOX`` gate is spelled
    ``f"{review_isolation.SANDBOX_EXEC} is not present on this host"``, so its reason has no
    literal form in the source at all. Rather than resolve it by importing the module -- an
    import that would run that module's own gates -- each interpolation becomes ``.+`` and
    every literal segment is matched EXACTLY, in order, anchored at both ends. A hole may
    not be empty, so the literal text still has to be right.
    """
    if not isinstance(node, ast.JoinedStr):
        return None
    display: list[str] = []
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            display.append(value.value)
            parts.append(re.escape(value.value))
        elif isinstance(value, ast.FormattedValue):
            display.append("{...}")
            parts.append(".+")
        else:                                    # pragma: no cover - defensive
            return None
    return "".join(display), re.compile("^" + "".join(parts) + "$", re.DOTALL)


def _guard_for(node: Any, constants: Mapping[str, str]) -> SkipGuard | None:
    """The guard a decorator ARGUMENT denotes: a literal, a module constant, or an f-string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _exact(node.value)
    if isinstance(node, ast.Name) and node.id in constants:
        return _exact(constants[node.id])
    if isinstance(node, ast.Attribute) and node.attr in constants:
        return _exact(constants[node.attr])
    return _joined_str_guard(node)


def _skip_call_guard(node: Any, constants: Mapping[str, str]) -> SkipGuard | None:
    """``unittest.skipUnless(cond, REASON)`` / ``skipIf(...)`` -> its guard, else ``None``."""
    if not isinstance(node, ast.Call):
        return None
    name = node.func.attr if isinstance(node.func, ast.Attribute) else (
        node.func.id if isinstance(node.func, ast.Name) else "")
    if name not in _SKIP_CALLS or len(node.args) < 2:
        return None
    return _guard_for(node.args[1], constants)


def _module_skip_constants(tree: Any) -> tuple[dict[str, str], dict[str, SkipGuard]]:
    """``({NAME: string}, {ALIAS: guard})`` for one module.

    The second map is what makes ``@DARWIN_ONLY`` resolvable: `test_review_isolation` builds
    its gates as module-level decorator objects, so the reason never appears at the
    decorated class at all.
    """
    constants: dict[str, str] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            constants[node.targets[0].id] = node.value.value
    aliases: dict[str, SkipGuard] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            guard = _skip_call_guard(node.value, constants)
            if guard is not None:
                aliases[node.targets[0].id] = guard
    return constants, aliases


def _decorator_guards(decorators: Sequence[Any], constants: Mapping[str, str],
                      aliases: Mapping[str, SkipGuard]) -> list[SkipGuard]:
    found: list[SkipGuard] = []
    for decorator in decorators:
        guard = _skip_call_guard(decorator, constants)
        if guard is not None:
            found.append(guard)
        elif isinstance(decorator, ast.Name) and decorator.id in aliases:
            found.append(aliases[decorator.id])
        elif isinstance(decorator, ast.Attribute) and decorator.attr in aliases:
            found.append(aliases[decorator.attr])
    return found


def _skip_test_guards(body: Sequence[Any], constants: Mapping[str, str]) -> list[SkipGuard]:
    """Every reason a ``self.skipTest(...)`` inside ``body`` can raise."""
    found: list[SkipGuard] = []
    for node in body:
        for child in ast.walk(node):
            if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                continue
            if child.func.attr != "skipTest" or not child.args:
                continue
            guard = _guard_for(child.args[0], constants)
            if guard is not None:
                found.append(guard)
    return found


def skip_guards_for(module_path: Path, class_name: str, method_name: str) -> list[SkipGuard]:
    """Every skip a single test id can raise, resolved from the source alone.

    The union of four sources, and no fifth:

    1. a ``skipUnless``/``skipIf`` decorator on the METHOD;
    2. the same on the METHOD'S OWN CLASS, including a module-level decorator alias;
    3. a ``self.skipTest("...")`` inside the method;
    4. a ``self.skipTest("...")`` inside the class's ``setUp`` or a NON-test helper of the
       same class -- `test_orca_runtime` and the OS-37 live suites both gate through one,
       and a gate is no less real for having a name.

    Another TEST'S ``skipTest`` is never a guard on this one, which is exactly the binding
    the module-wide check lacked. A class the module does not define yields ``[]``, so a
    manifest entry naming a test that no longer exists fails rather than passing vacuously.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    constants, aliases = _module_skip_constants(tree)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        guards = _decorator_guards(node.decorator_list, constants, aliases)
        method = next((item for item in node.body
                       if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and item.name == method_name), None)
        if method is None:
            # FAIL-CLOSED, and this is the binding's whole point: a manifest entry naming a
            # method this class does not define must not inherit its class's gate and pass.
            # A renamed or deleted test is exactly the drift this check exists to catch.
            return []
        guards += _decorator_guards(method.decorator_list, constants, aliases)
        guards += _skip_test_guards(method.body, constants)
        for item in node.body:
            if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name != method_name and not item.name.startswith("test_")):
                guards += _skip_test_guards(item.body, constants)
        return guards
    return []


def skip_guard_binds(test_id: str, reason: str, *, root: Path | None = None) -> bool:
    """Whether the DECLARED test really is guarded by a skip carrying ``reason``."""
    module, _, rest = test_id.partition(".")
    class_name, _, method_name = rest.partition(".")
    path = (root or REPO_ROOT / "scripts") / f"{module}.py"
    if not path.exists():
        return False
    return any(pattern.match(reason)
               for _display, pattern in skip_guards_for(path, class_name, method_name))


def expected_tolerated_skips(
    alternatives: dict[str, list[tuple[str, str]]] | None = None,
    *, conditions: dict[str, Callable[[], bool]] | None = None,
) -> frozenset[tuple[str, str]]:
    """Resolve the declaration against an environment: what must skip HERE, and why.

    A test whose conditions all evaluate false is expected to RUN, and is therefore absent
    from the returned set -- which is what makes the contract exact in both directions on
    both platforms. On macOS the 22 platform-gated tests resolve to nothing and must run; on
    Linux they resolve to a skip each and must skip.
    """
    declared = alternatives if alternatives is not None else load_tolerated_alternatives()
    predicates = conditions or CONDITIONS
    expected = set()
    for test_id, entries in declared.items():
        for condition, reason in entries:
            if predicates[condition]():
                expected.add((test_id, reason))
                break
    return frozenset(expected)


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
        tolerated = expected_tolerated_skips()

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

    `tolerated` is the declaration already RESOLVED against this environment by
    :func:`expected_tolerated_skips`, so both directions are exact on every platform: on
    Linux the 22 platform-gated tests are in the set and must skip, on macOS they are not
    and must run. Neither host gets a weaker contract than the other.

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

    here = f"platform={sys.platform} conditions={','.join(holding_conditions())}"
    if still_missing:
        problems.append(
            f"lane {lane!r} did NOT produce {len(still_missing)} skip(s) that "
            f"{TOLERATED_SKIP_MANIFEST.name} declares as expected HERE ({here}). Either "
            "the test now runs under a condition that says it should skip, or it was "
            "renamed or deleted: " + _sample(still_missing)
            + f" -- reconcile {TOLERATED_SKIP_MANIFEST.name}")
    if still_excess:
        problems.append(
            f"lane {lane!r} produced {len(still_excess)} skip(s) that "
            f"{TOLERATED_SKIP_MANIFEST.name} does not expect HERE ({here}). A skip is "
            "tolerated by IDENTITY under a stated CONDITION, never by wording and never "
            "unconditionally, so an additional test carrying an already-declared reason is "
            "still a failure: " + _sample(still_excess)
            + f" -- fix the test, or declare it in {TOLERATED_SKIP_MANIFEST.name} under the "
              "condition it actually skips under")
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
        tolerated = expected_tolerated_skips()
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
        f"tolerated skips: platform={sys.platform} "
        f"conditions={'+'.join(holding_conditions())} "
        f"expected-here={len(tolerated)} "
        f"matched={len(tolerated & actual_tolerated)} "
        f"missing={len(tolerated - actual_tolerated)} "
        f"unexpected={len(actual_tolerated - tolerated)}")


# The child that reports which tests DECLARE themselves skipped under a given environment.
#
# It reads `__unittest_skip__` / `__unittest_skip_why__` -- the very attributes
# `TestCase.run` consults -- so the reasons it returns are the ones the runner produces,
# not reasons this repository invented. No test body executes.
#
# `sys.platform` is patched AFTER the platform-sensitive third-party modules are already in
# `sys.modules`. Patching it first breaks CPython's own import machinery (it looks for
# `_sysconfigdata__linux_darwin`), which silently turns unrelated LangGraph tests into
# skips -- an observation that would be wrong in a way that looks plausible. The child
# asserts it produced no such artifact before printing anything.
_PLATFORM_PROBE = r'''
import json, sys, unittest
from pathlib import Path

platform, sandbox_present, sandbox_exec = json.loads(sys.argv[1])
sys.path.insert(0, ".")
if platform != sys.platform:
    try:
        import langgraph, langgraph.graph  # noqa: F401  (cache BEFORE the patch)
    except ImportError:
        pass
    sys.platform = platform
# Symmetric on purpose: the sandbox binary is made to look PRESENT as readily as absent, so
# that the "no condition holds" environment can be observed from a host that lacks it.
_real_exists = Path.exists


def _patched_exists(self):
    return sandbox_present if str(self) == sandbox_exec else _real_exists(self)


Path.exists = _patched_exists

suite = unittest.TestLoader().discover(start_dir="scripts", pattern="test_*.py")


def walk(item):
    if isinstance(item, unittest.TestSuite):
        for child in item:
            yield from walk(child)
    else:
        yield item


rows = []
for test in walk(suite):
    method = getattr(test, test._testMethodName, None)
    if (getattr(test.__class__, "__unittest_skip__", False)
            or getattr(method, "__unittest_skip__", False)):
        why = (getattr(test.__class__, "__unittest_skip_why__", "")
               or getattr(method, "__unittest_skip_why__", ""))
        rows.append([test.id(), why])

print("PROBE_JSON " + json.dumps(sorted(rows)))
'''


def _run_platform_probe(platform: str, sandbox_present: bool) -> list[tuple[str, str]]:
    argument = json.dumps([platform, sandbox_present, SANDBOX_EXEC])
    completed = subprocess.run(
        [sys.executable, "-c", _PLATFORM_PROBE, argument],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"platform probe failed for platform={platform!r} "
            f"sandbox_present={sandbox_present}: {completed.stderr.strip()}")
    for line in completed.stdout.splitlines():
        if line.startswith("PROBE_JSON "):
            return [(test_id, why) for test_id, why in
                    json.loads(line[len("PROBE_JSON "):])]
    raise RuntimeError(f"platform probe printed no result: {completed.stdout!r}")


def observe_declared_skips(*, platform: str, sandbox_present: bool) -> list[tuple[str, str]]:
    """What `unittest` would declare skipped under the given environment, observed.

    Runs in SUBPROCESSES: the patching it needs is exactly the kind that must not leak into
    the process making decisions about it.

    Two runs, not one. The patched run alone cannot tell a real platform gate from an
    artifact of the patch itself -- patching ``sys.platform`` can break CPython's import
    machinery and silently turn LangGraph tests into skips. So the unpatched BASELINE is
    observed in the same interpreter and the difference is what is judged: a LangGraph gate
    that moves *because* we said "linux" is corruption and raises, while LangGraph gates
    that were already skipping (the dependency-absent lane, where all 240 legitimately do)
    are simply not the platform probe's business and are filtered out.
    """
    baseline = _run_platform_probe(sys.platform, Path(SANDBOX_EXEC).exists())
    observed = _run_platform_probe(platform, sandbox_present)
    baseline_langgraph = {test_id for test_id, why in baseline if is_langgraph_skip(why)}
    moved = sorted(test_id for test_id, why in observed
                   if is_langgraph_skip(why) and test_id not in baseline_langgraph)
    if moved:
        raise RuntimeError(
            f"PLATFORM_PROBE_CORRUPTED: simulating platform={platform!r} moved "
            f"{len(moved)} LangGraph gate(s) that were not skipping before it. A LangGraph "
            "gate is a dependency gate and must not depend on the platform, so this "
            "observation would be wrong in a way that looks plausible: "
            + ", ".join(moved[:3]))
    # LangGraph skips belong to the other manifest; the platform derivation must not claim
    # them, or the absent lane would declare 240 tests as platform-conditional.
    return [(test_id, why) for test_id, why in observed if not is_langgraph_skip(why)]


# ---- OS-37 correction iteration 6: the gates the SOURCE declares, held to the manifest --
# CI on 423bcb7 failed the absent lane with 30 LangGraph skips the manifest did not declare,
# from two modules whose gated classes had been added without regenerating it. Nothing
# local had been able to see that. The absent lane refuses to run on a host that has
# langgraph, and in the present lane `skipUnless(True, ...)` hands the class back untouched
# -- the gate leaves no attribute to read -- so a green present lane, and a green
# `test_ci_lanes`, said nothing about whether the manifest was complete.
#
# This probe makes the gate visible from EITHER lane. It loads the suite in a child whose
# import system refuses `langgraph`, so every import-time `_langgraph_ok()` evaluates False
# and every `skipUnless` gate sets the same `__unittest_skip__` / `__unittest_skip_why__`
# attributes `TestCase.run` consults -- on the class it decorates and, through inheritance,
# on every fixture subclass in every module, which is the binding the AST walk in
# `skip_guards_for` cannot follow across modules. The set it reports is the set the absent
# lane would skip at LOAD time, observed without being in that lane. No test body executes.
#
# Stated rather than implied: a gate raised at RUN time -- `self.skipTest(...)` after an
# ImportError inside the test body -- leaves no attribute and is invisible here. Such a test
# is reported only as COLLECTED, which is all the probe can honestly say about it; the
# absent lane's identity check still holds it, and `test_ci_lanes` names each one so that a
# second is a decision rather than drift.
_LANGGRAPH_REFUSED_PROBE = r'''
import json, sys, unittest


class _RefuseLangGraph:
    """A finder ahead of every other: in this child, `langgraph` does not exist."""

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] == "langgraph":
            raise ModuleNotFoundError("langgraph is refused in this probe", name=name)
        return None


sys.meta_path.insert(0, _RefuseLangGraph())
sys.path.insert(0, ".")
suite = unittest.TestLoader().discover(start_dir="scripts", pattern="test_*.py")
assert "langgraph" not in sys.modules, "the refusal did not hold"


def walk(item):
    if isinstance(item, unittest.TestSuite):
        for child in item:
            yield from walk(child)
    else:
        yield item


collected, declared, unimportable = [], [], []
for test in walk(suite):
    if isinstance(test, unittest.loader._FailedTest):
        unimportable.append(test.id())
        continue
    collected.append(test.id())
    method = getattr(test, test._testMethodName, None)
    if (getattr(test.__class__, "__unittest_skip__", False)
            or getattr(method, "__unittest_skip__", False)):
        why = (getattr(test.__class__, "__unittest_skip_why__", "")
               or getattr(method, "__unittest_skip_why__", ""))
        declared.append([test.id(), why])

print("PROBE_JSON " + json.dumps({"collected": sorted(collected),
                                  "declared": sorted(declared),
                                  "unimportable": sorted(unimportable)}))
'''


class LangGraphGateProbe(NamedTuple):
    """What the suite declares when `langgraph` is refused at import: the OBSERVATION."""

    #: Every test id the loader collected -- the ids the lanes run.
    collected: frozenset[str]
    #: `{test id: reason}` for every test declared skipped at load time, for ANY reason.
    declared: dict[str, str]
    #: Modules the loader could not import with `langgraph` refused; the absent lane would
    #: ERROR on these rather than skip, so they are a defect in their own right.
    unimportable: tuple[str, ...]

    @property
    def langgraph_gated(self) -> frozenset[str]:
        return frozenset(test_id for test_id, why in self.declared.items()
                         if is_langgraph_skip(why))


def observe_declared_langgraph_gates() -> LangGraphGateProbe:
    """Load the suite with `langgraph` refused and read what declares itself skipped.

    A SUBPROCESS, for the same reason as the platform probe: the import refusal must not
    leak into the interpreter that judges the result.
    """
    completed = subprocess.run(
        [sys.executable, "-c", _LANGGRAPH_REFUSED_PROBE],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"langgraph-refused probe failed: {completed.stderr.strip()}")
    for line in completed.stdout.splitlines():
        if line.startswith("PROBE_JSON "):
            payload = json.loads(line[len("PROBE_JSON "):])
            return LangGraphGateProbe(
                collected=frozenset(payload["collected"]),
                declared={test_id: why for test_id, why in payload["declared"]},
                unimportable=tuple(payload["unimportable"]))
    raise RuntimeError(f"langgraph-refused probe printed no result: {completed.stdout!r}")


def check_declared_langgraph_gates(probe: LangGraphGateProbe,
                                   expected: frozenset[str] | None = None) -> list[str]:
    """Hold the manifest to the gates the source declares. Runs in BOTH lanes.

    Three problems, each the present-lane-visible form of something only the absent lane
    used to be able to say:

    * a test DECLARES a LangGraph gate the manifest does not list -- the absent lane's
      `unexpected`, which is exactly the 423bcb7 failure, now visible from a host that has
      langgraph installed;
    * a test module cannot be IMPORTED without langgraph -- the absent lane's ERROR;
    * a manifest entry names a test the suite no longer COLLECTS -- the present lane's
      `never_ran`, without needing to run anything.
    """
    if expected is None:
        expected = load_expected_langgraph_skips()
    problems: list[str] = []
    if probe.unimportable:
        problems.append(
            f"{len(probe.unimportable)} test module(s) cannot be imported without "
            "langgraph, so the dependency-absent lane would ERROR on them rather than "
            "skip: "
            + _sample(sorted(probe.unimportable)))
    undeclared = probe.langgraph_gated - expected
    if undeclared:
        problems.append(
            f"{len(undeclared)} test(s) declare a LangGraph gate that "
            f"{LANGGRAPH_SKIP_MANIFEST.name} does not list. The dependency-absent lane "
            "will fail on them as 'unexpected', and coverage has left that lane without "
            "anyone deciding it should: " + _sample(sorted(undeclared))
            + " -- regenerate the manifest from an environment WITHOUT langgraph "
              "(`ci_lane --lane absent write-manifest`) and read the diff")
    uncollected = expected - probe.collected
    if uncollected:
        problems.append(
            f"{len(uncollected)} entr(y/ies) in {LANGGRAPH_SKIP_MANIFEST.name} name a test "
            "the suite no longer collects; it was deleted or renamed: "
            + _sample(sorted(uncollected)) + " -- regenerate the manifest")
    return problems


def derive_tolerated_alternatives(
    observed_here: set[tuple[str, str]],
) -> dict[str, list[tuple[str, str]]]:
    """Build the full, multi-platform declaration -- never a one-host view.

    Four observations, because one host cannot see the whole contract:

    * ``darwin`` + sandbox-exec present -- the environment in which NO platform condition
      holds. Whatever still declares itself skipped there is gated on something the
      platform conditions do not describe (an opt-in env var, for the live suites), and
      that observation is what separates a platform gate from an unconditional one.
    * ``linux`` + no sandbox-exec -- every platform-gated test, each with the reason that
      WINS when both gates hold (the outermost decorator's, which is how `unittest`
      resolves it). These become the ``not_darwin`` alternatives.
    * ``darwin`` + no sandbox-exec -- the tests that skip for the sandbox binary alone.
      These become the ``no_sandbox_exec`` alternatives, listed second so they apply on a
      darwin host missing the binary while `not_darwin` applies everywhere else.
    * this host, at runtime -- everything that skipped for a reason no platform gate
      explains. Those are ``always``.

    A test carrying both gates therefore gets TWO lines and stays exact on both platforms,
    which is the whole point: adding the 22 unconditionally would be green on Linux and red
    on macOS, and observing only this host would be the reverse.

    Why the first observation is not optional (OS-37 final review, R6): a decorator-style
    env-var gate such as ``@unittest.skipUnless(E2E_ENABLED, ...)`` is visible to the
    load-time probe in EVERY simulated environment. Without the unconditional baseline the
    two platform simulations agree on it, and agreement between them was read as "the
    sandbox binary alone explains it" -- 26 opt-in live-suite tests came out as
    ``no_sandbox_exec``, a file that expected 6 skips on a Mac instead of 32. A skip that
    is ALSO present when no condition holds is explained by neither condition.
    """
    unconditional_reasons = dict(
        observe_declared_skips(platform="darwin", sandbox_present=True))
    linux_rows = observe_declared_skips(platform="linux", sandbox_present=False)
    darwin_rows = observe_declared_skips(platform="darwin", sandbox_present=False)
    darwin_reasons = dict(darwin_rows)

    alternatives: dict[str, list[tuple[str, str]]] = {}
    for test_id, reason in linux_rows:
        if unconditional_reasons.get(test_id) == reason:
            # Skips identically when no platform condition holds: not a platform gate.
            # It reaches the manifest through the runtime observation below, as `always`.
            continue
        sandbox_reason = darwin_reasons.get(test_id)
        if sandbox_reason is not None and unconditional_reasons.get(test_id) == sandbox_reason:
            # The darwin-no-sandbox simulation shows the same reason the unconditional
            # environment does, so the missing binary is not what this arm records.
            sandbox_reason = None
        if sandbox_reason == reason:
            # The sandbox binary alone explains it; there is no darwin-specific arm.
            alternatives.setdefault(test_id, []).append(("no_sandbox_exec", reason))
            continue
        alternatives.setdefault(test_id, []).append(("not_darwin", reason))
        if sandbox_reason is not None:
            alternatives[test_id].append(("no_sandbox_exec", sandbox_reason))

    platform_gated = set(alternatives)
    for test_id, reason in sorted(observed_here):
        if test_id not in platform_gated:
            alternatives.setdefault(test_id, []).append(("always", reason))
    return alternatives


def render_tolerated_manifest(alternatives: dict[str, list[tuple[str, str]]]) -> str:
    """Serialise, with provenance. The header is not decoration.

    It records the host the observation came from and states which conditions were observed
    directly versus simulated, so a reader can never mistake a one-platform view for a
    universal one -- and `write_manifest` refuses to emit a file that has no conditional
    arm while the source still declares platform gates.
    """
    lines = [
        f"# generated on: platform={sys.platform} "
        f"sandbox_exec_present={str(Path(SANDBOX_EXEC).exists()).lower()}",
        "# conditions: 'always' observed at runtime on this host; 'not_darwin' and",
        "#   'no_sandbox_exec' observed by loading the suite under a simulated environment",
        "#   and reading the same __unittest_skip_why__ attributes TestCase.run consults.",
        "#",
    ]
    for condition in CONDITIONS:
        rows = sorted((test_id, reason)
                      for test_id, entries in alternatives.items()
                      for name, reason in entries if name == condition)
        if not rows:
            continue
        lines.append(f"# -- {condition} ({len(rows)}) --")
        lines.extend(f"{condition}\t{test_id}\t{reason}" for test_id, reason in rows)
    return TOLERATED_MANIFEST_HEADER + "\n".join(lines) + "\n"

#: The decorators that ARE the platform gates, and the one module that declares them. The
#: anti-drift check in `test_ci_lanes` reads these names out of the AST and holds the
#: checked-in manifest to them; the writer below holds its OWN output to the same reading
#: before it is allowed to become the checked-in manifest.
PLATFORM_GATE_DECORATORS = {"DARWIN_ONLY": "not_darwin", "NEEDS_SANDBOX": "no_sandbox_exec"}
PLATFORM_GATED_MODULE = "test_review_isolation"


def declared_platform_gates(root: Path | None = None) -> dict[str, set[str]]:
    """`{test id: {condition, ...}}` -- the platform gates the SOURCE declares, from the AST.

    Independent of the observation model on purpose: it never loads the suite and never
    reads a ``__unittest_skip__`` attribute. It is the reading `test_ci_lanes` applies to
    the checked-in file, made available to the writer so that a derivation which disagrees
    with the source is refused at generation time rather than discovered by the next test
    run.
    """
    path = (root or REPO_ROOT) / "scripts" / f"{PLATFORM_GATED_MODULE}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def conditions(decorators: Sequence[Any]) -> set[str]:
        return {PLATFORM_GATE_DECORATORS[node.id] for node in decorators
                if isinstance(node, ast.Name) and node.id in PLATFORM_GATE_DECORATORS}

    gates: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        class_conditions = conditions(node.decorator_list)
        for member in node.body:
            if isinstance(member, ast.FunctionDef) and member.name.startswith("test"):
                found = class_conditions | conditions(member.decorator_list)
                if found:
                    gates[f"{PLATFORM_GATED_MODULE}.{node.name}.{member.name}"] = found
    return gates


def check_tolerated_derivation(
    alternatives: dict[str, list[tuple[str, str]]],
    gates: dict[str, set[str]] | None = None,
) -> list[str]:
    """The anti-drift invariant, applied to a DERIVED declaration before it is written.

    Both directions of `ManifestMatchesTheDeclaredGatesTests`: every platform arm the
    derivation emits must be a gate the source declares for that very test, and every gate
    the source declares must have its arm. A derivation that fails this is not a manifest,
    it is the observation model being wrong -- which is exactly what happened when the
    simulated environments agreed on an env-var gate and 26 opt-in tests were emitted under
    ``no_sandbox_exec`` (OS-37 final review, R6). The writer exits non-zero on it and
    leaves the checked-in file alone.
    """
    declared = gates if gates is not None else declared_platform_gates()
    problems: list[str] = []
    for test_id in sorted(set(alternatives) | set(declared)):
        emitted = {condition for condition, _ in alternatives.get(test_id, [])
                   if condition != "always"}
        expected = declared.get(test_id, set())
        if emitted != expected:
            problems.append(
                f"TOLERATED_DERIVATION_DRIFT: the derivation expects {test_id} to skip "
                f"under {sorted(emitted)}, but the source declares {sorted(expected)}")
    return problems


def write_manifest(lane: str, *, verbosity: int = 0) -> int:
    """Regenerate LANGGRAPH_SKIP_MANIFEST from a real dependency-absent run.

    Refuses to run in the present lane: there is nothing to record there, and writing an
    empty manifest would silently disarm every assertion that depends on it.

    Nothing is written until BOTH files have been derived and validated: a refusal leaves
    the checked-in pair exactly as it found them, never one regenerated and one stale.
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

    # The other half of the contract, derived from the same run so the two cannot be
    # generated against different trees. An EMPTY tolerated set is legitimate -- it means
    # nothing outside the LangGraph gates skips -- so, unlike the manifest above, it is
    # written rather than refused.
    alternatives = derive_tolerated_alternatives(tolerated_pairs(result))
    counts = {name: sum(1 for entries in alternatives.values()
                        for condition, _ in entries if condition == name)
              for name in CONDITIONS}

    # The guard the review asked for: a one-platform observation may never be emitted as if
    # it were universal. `scripts/test_review_isolation.py` declares platform gates, so a
    # manifest with no conditional arm can only mean the simulation silently produced
    # nothing -- which would hand CI a file that is exact here and wrong on the runner.
    conditional = counts["not_darwin"] + counts["no_sandbox_exec"]
    if not conditional:
        print("CI_LANE_ERROR: the derived manifest has no platform-conditional entries, but "
              "the suite declares platform gates. Refusing to write a one-platform view as "
              "if it were universal.", file=sys.stderr)
        return 1

    # Fail closed (OS-37 final review, R6): the derivation is held to the gates the source
    # declares BEFORE it may become the checked-in file. Exit 0 after emitting a manifest
    # that the suite's own anti-drift test rejects is not success, it is drift with a
    # green light; the refusal names every disagreeing entry and writes nothing.
    drift = check_tolerated_derivation(alternatives)
    if drift:
        for problem in drift:
            print(f"CI_LANE_ERROR: {problem}", file=sys.stderr)
        print(f"CI_LANE_ERROR: TOLERATED_DERIVATION_DRIFT: refusing to write "
              f"{TOLERATED_SKIP_MANIFEST.name} ({len(drift)} entries disagree with the "
              f"gates {PLATFORM_GATED_MODULE}.py declares); neither manifest was written",
              file=sys.stderr)
        return 1

    LANGGRAPH_SKIP_MANIFEST.write_text(MANIFEST_HEADER + "\n".join(ids) + "\n",
                                       encoding="utf-8")
    print(f"wrote {len(ids)} test ids to {LANGGRAPH_SKIP_MANIFEST}")
    TOLERATED_SKIP_MANIFEST.write_text(render_tolerated_manifest(alternatives),
                                       encoding="utf-8")
    print(f"wrote {sum(counts.values())} tolerated-skip declarations for "
          f"{len(alternatives)} tests to {TOLERATED_SKIP_MANIFEST} "
          + " ".join(f"{name}={counts[name]}" for name in CONDITIONS))
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
