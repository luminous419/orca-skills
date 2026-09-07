"""OS-42: artifact identity, the one-based gate_iteration derivation, and drift.

The gate_iteration tests are the F-002 correction: `phase_iterations[phase]` is
zero-based and post-incremented, so feeding it raw to a one-based suffix ladder gives
review 1 the path `REVIEW_<PHASE>_iteration0.md` and maps review 2 onto review 1's
unsuffixed file -- silently overwriting evidence.
"""
from __future__ import annotations

import ast
import hashlib
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import release_manifest, task_context
from scripts.deterministic_workflow import artifact_identity
from scripts.deterministic_workflow.artifact_identity import (ArtifactIdentityError,
                                                              artifact_relative_path,
                                                              gate_iteration)
from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES, make_intent
from scripts.deterministic_workflow.state import initial_state

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "orca-worker-reviewer-orchestration"
ENGINE = REPO_ROOT / "scripts" / "deterministic_workflow"
INSTALLED_ENGINE = SKILL / "tools" / "deterministic_workflow"


def state_with(phase_iterations: int, final_review_iterations: int = 0):
    state = dict(initial_state(run_id="run_os42", thread_id="t", phases=("ANALYSIS",),
                               capabilities=frozenset(BASE_CAPABILITIES), risk="high"))
    state["phase_iterations"]["ANALYSIS"] = phase_iterations
    state["remaining_phase_budget"]["ANALYSIS"] = 5 - phase_iterations
    state["final_review_iterations"] = final_review_iterations
    state["remaining_final_budget"] = 5 - final_review_iterations
    return state


class GateIterationTests(unittest.TestCase):
    def test_gate_iteration_is_one_based_for_every_role(self) -> None:
        """The FIRST attempt of every role derives 1, never 0.

        The raw-counter feed the design originally specified fails on the very first row.
        """
        state = state_with(0, final_review_iterations=1)
        for role in ("WORKER", "PHASE_REVIEWER", "FINAL_REVIEWER"):
            with self.subTest(role=role):
                self.assertEqual(gate_iteration(state, role, "ANALYSIS"), 1)

    def test_high_risk_worker_completion_does_not_advance_the_reviewer_ordinal(self) -> None:
        """The engine fact the defect rested on: at risk=high a COMPLETE Worker leaves
        the counter at 0, so the first Reviewer must still derive 1.

        Catches a "fix" that adds +1 only for Workers, or that special-cases risk inside
        the derivation instead of relying on the counter's own risk semantics.
        """
        self.assertEqual(gate_iteration(state_with(0), "PHASE_REVIEWER", "ANALYSIS"), 1)
        self.assertEqual(gate_iteration(state_with(1), "PHASE_REVIEWER", "ANALYSIS"), 2)

    def test_low_risk_worker_ordinal_advances_on_completion(self) -> None:
        """The mirror image: catches a derivation that hard-codes 1 for Workers."""
        self.assertEqual(gate_iteration(state_with(0), "WORKER", "ANALYSIS"), 1)
        self.assertEqual(gate_iteration(state_with(1), "WORKER", "ANALYSIS"), 2)

    def test_final_reviewer_ordinal_is_not_incremented_twice(self) -> None:
        """`final_review_iterations` is PRE-incremented by prepare_intent_node.

        Catches applying the Worker formula uniformly, which would skip FINAL_REVIEW.md
        entirely and start the family at _iteration2.
        """
        self.assertEqual(
            gate_iteration(state_with(0, 1), "FINAL_REVIEWER", "ANALYSIS"), 1)
        self.assertEqual(
            gate_iteration(state_with(0, 2), "FINAL_REVIEWER", "ANALYSIS"), 2)

    def test_gate_iteration_zero_is_refused_not_rendered(self) -> None:
        """Catches clamping or defaulting instead of failing closed."""
        with self.assertRaises(ArtifactIdentityError):
            gate_iteration(state_with(0, 0), "FINAL_REVIEWER", "ANALYSIS")
        with self.assertRaises(ArtifactIdentityError):
            artifact_relative_path(run_id="r", phase="ANALYSIS", role="WORKER",
                                   gate_iteration=0)

    def test_a_bool_is_not_an_ordinal(self) -> None:
        with self.assertRaises(ArtifactIdentityError):
            artifact_relative_path(run_id="r", phase="ANALYSIS", role="WORKER",
                                   gate_iteration=True)

    def test_derived_field_equals_the_two_expressions_it_replaces(self) -> None:
        """Proves the single-sourcing is a REFACTOR of the adapter expression, not a
        behaviour change, and catches a future edit that moves one consumer only."""
        for phase_iterations in (0, 1, 2):
            for final_review_iterations in (1, 2):
                for role in ("WORKER", "PHASE_REVIEWER", "FINAL_REVIEWER"):
                    with self.subTest(role=role, pi=phase_iterations):
                        state = state_with(phase_iterations, final_review_iterations)
                        intent = make_intent(state, role, "PHASE_GATE")
                        adapter_expression = (
                            intent["final_review_iteration"]
                            if role == "FINAL_REVIEWER"
                            else intent["phase_iteration"] + 1)
                        self.assertEqual(intent["gate_iteration"], adapter_expression)


class ArtifactPathTests(unittest.TestCase):
    def test_two_ordinary_phase_reviewer_iterations_get_distinct_paths(self) -> None:
        """The defect in one assertion: under the raw-counter rule these were
        `_iteration0` and the unsuffixed name, so review 2 overwrote review 1."""
        first = artifact_relative_path(run_id="run_os42", phase="ANALYSIS",
                                       role="PHASE_REVIEWER", gate_iteration=1)
        second = artifact_relative_path(run_id="run_os42", phase="ANALYSIS",
                                        role="PHASE_REVIEWER", gate_iteration=2)
        self.assertEqual(first, "artifacts/runs/run_os42/REVIEW_ANALYSIS.md")
        self.assertEqual(second, "artifacts/runs/run_os42/REVIEW_ANALYSIS_iteration2.md")
        self.assertNotEqual(first, second)
        self.assertNotIn("_iteration0", first)

    def test_a_repair_reuses_its_owning_gate_iterations_path(self) -> None:
        """`repair_attempt` is not a parameter, so it CANNOT be plumbed in by accident.

        Two dispatches of the same gate attempt resolve to one file; two gate attempts
        resolve to two.
        """
        state = state_with(1)
        ordinary = make_intent(state, "PHASE_REVIEWER", "PHASE_GATE")
        repairing = dict(state, repair_attempts=1, remaining_repair_budget=1,
                         pending_gate_defect={"code": "DECISION_GATE_FORM_DEFECT",
                                              "defects": [{"code": "c", "kind": "FORM",
                                                           "field_path": "f",
                                                           "expected": [], "actual": "a",
                                                           "message": "m"}]})
        repair = make_intent(repairing, "PHASE_REVIEWER", "PHASE_GATE")
        self.assertEqual(ordinary["artifact_contract_path"],
                         repair["artifact_contract_path"])
        self.assertEqual(ordinary["gate_iteration"], repair["gate_iteration"])
        self.assertNotEqual(ordinary["command_id"], repair["command_id"])

    def test_a_worker_artifact_never_gains_a_suffix(self) -> None:
        for iteration in (1, 2, 7):
            with self.subTest(iteration=iteration):
                self.assertEqual(
                    artifact_relative_path(run_id="r", phase="ANALYSIS", role="WORKER",
                                           gate_iteration=iteration),
                    "artifacts/runs/r/ANALYSIS.md")

    def test_the_final_review_family(self) -> None:
        self.assertEqual(
            artifact_relative_path(run_id="r", phase="final_review",
                                   role="FINAL_REVIEWER", gate_iteration=1),
            "artifacts/runs/r/FINAL_REVIEW.md")
        self.assertEqual(
            artifact_relative_path(run_id="r", phase="final_review",
                                   role="FINAL_REVIEWER", gate_iteration=3),
            "artifacts/runs/r/FINAL_REVIEW_iteration3.md")

    def test_no_iteration1_form_exists_anywhere(self) -> None:
        """SKILL.md states the `_iteration1` form exists nowhere."""
        for role in ("worker", "reviewer", "final_reviewer"):
            for phase in ("ANALYSIS", "final_review"):
                with self.subTest(role=role, phase=phase):
                    path = artifact_relative_path(run_id="r", phase=phase, role=role,
                                                  gate_iteration=1)
                    self.assertNotIn("_iteration1", path)

    def test_engine_and_task_context_agree_on_every_artifact_path(self) -> None:
        """One definition, four consumers."""
        for role in ("worker", "reviewer"):
            for phase in ("analysis", "plan", "test"):
                for iteration in (1, 2, 5):
                    with self.subTest(role=role, phase=phase, iteration=iteration):
                        self.assertEqual(
                            task_context.phase_artifact_contract(
                                role=role, phase=phase, run_id="r",
                                gate_iteration=iteration),
                            artifact_relative_path(run_id="r", phase=phase, role=role,
                                                   gate_iteration=iteration))

    def test_the_three_former_ladders_delegate(self) -> None:
        """`e2e_harness`, `run_logging` and `review_isolation` each carried their own
        copy of `"" if attempt == 1 else f"_iteration{attempt}"`. Catches a fourth copy
        being reintroduced."""
        pattern = re.compile(r'if attempt == 1 else f"_iteration\{attempt\}"')
        for name in ("e2e_harness.py", "run_logging.py", "review_isolation.py"):
            with self.subTest(module=name):
                source = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")
                self.assertIsNone(pattern.search(source),
                                  f"{name} still carries its own suffix ladder")

    def test_the_skill_artifact_path_rule_matches_the_function(self) -> None:
        """The document and the code cannot drift."""
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("<ARTIFACT_ROOT>FINAL_REVIEW_iteration<N>.md", text)
        self.assertIn("<ARTIFACT_ROOT>REVIEW_<PHASE>_iteration<N>.md", text)
        self.assertIn("`_iteration1` 형태는 어디에도 존재하지 않는다", text)


class InstalledCopyTests(unittest.TestCase):
    def test_installed_engine_copy_is_byte_identical(self) -> None:
        """Nothing asserted this before OS-42; a schema change made in `scripts/` and
        forgotten in `tools/` would have shipped silently."""
        source = {p.name: p.read_bytes() for p in ENGINE.glob("*.py")}
        installed = {p.name: p.read_bytes() for p in INSTALLED_ENGINE.glob("*.py")}
        self.assertEqual(set(source), set(installed))
        for name in sorted(source):
            with self.subTest(module=name):
                self.assertEqual(source[name], installed[name])

    def test_installed_contract_modules_are_byte_identical(self) -> None:
        for module in release_manifest.DECISION_CONTRACT_CLOSURE:
            with self.subTest(module=module):
                self.assertEqual(
                    (REPO_ROOT / "scripts" / f"{module}.py").read_bytes(),
                    (SKILL / "tools" / f"{module}.py").read_bytes())

    @staticmethod
    def local_import_closure(*roots: str) -> set[str]:
        """Every `scripts/*.py` module reachable from `roots` by a local import.

        Recomputed by walking ASTs rather than asserted from a list, so a future import
        added to any member fails the test that consumes it until the manifest is
        updated.
        """
        local = {p.stem for p in (REPO_ROOT / "scripts").glob("*.py")}
        seen: set[str] = set()
        stack = list(roots)
        while stack:
            stem = stack.pop()
            if stem in seen:
                continue
            seen.add(stem)
            tree = ast.parse((REPO_ROOT / "scripts" / f"{stem}.py").read_text("utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    head = name.replace("scripts.", "").split(".")[0]
                    if head in local and head not in seen:
                        stack.append(head)
        return seen

    def test_installed_contract_modules_are_self_contained(self) -> None:
        """The closure is RECOMPUTED, not asserted from a list.

        A future import added to any of the six fails here until the manifest is updated,
        which is what turns the D-1 fix from a one-time correction into a standing check.
        """
        self.assertEqual(self.local_import_closure("decision_contract"),
                         set(release_manifest.DECISION_CONTRACT_CLOSURE))

    # ---- OS-42 F-002: the PRODUCTION Orca path's closure ------------------------------

    def test_installed_orca_runtime_is_self_contained(self) -> None:
        """`OrcaAdapter` is only a production path if the runtime it is handed ships too.

        The finding was that `orca_runtime_harness` lived in this repository alone, so an
        installed launcher could offer nothing but the fake adapter. The two manifest
        lists together must be exactly the harness's own recomputed closure -- no more
        (which would ship a module nothing imports) and no less (an ImportError on the
        first production dispatch).
        """
        closure = self.local_import_closure("orca_runtime_harness")
        shipped = (set(release_manifest.DECISION_CONTRACT_CLOSURE)
                   | set(release_manifest.ORCA_RUNTIME_CLOSURE)
                   | {"run_logging", "clarification_protocol"})
        self.assertEqual(closure - shipped, set(),
                         "the installed Orca runtime imports a repository-only module")
        self.assertEqual(set(release_manifest.ORCA_RUNTIME_CLOSURE) - closure, set(),
                         "the manifest ships a module the Orca runtime never imports")

    def test_installed_orca_runtime_modules_are_byte_identical(self) -> None:
        for module in release_manifest.ORCA_RUNTIME_CLOSURE:
            with self.subTest(module=module):
                self.assertEqual(
                    (REPO_ROOT / "scripts" / f"{module}.py").read_bytes(),
                    (SKILL / "tools" / f"{module}.py").read_bytes())

    def test_the_manifest_requires_the_orca_runtime_closure(self) -> None:
        paths = release_manifest.required_skill_paths(
            release_manifest.ORCHESTRATION_SKILL_NAME)
        for module in release_manifest.ORCA_RUNTIME_CLOSURE:
            self.assertIn(
                f"{release_manifest.ORCHESTRATION_SKILL_NAME}/tools/{module}.py", paths)

    def test_the_manifest_lists_exactly_the_closure(self) -> None:
        paths = release_manifest.required_skill_paths(
            release_manifest.ORCHESTRATION_SKILL_NAME)
        for module in release_manifest.DECISION_CONTRACT_CLOSURE:
            self.assertIn(
                f"{release_manifest.ORCHESTRATION_SKILL_NAME}/tools/{module}.py", paths)

    def test_the_release_tree_still_verifies(self) -> None:
        """`verify_source_tree` rejects BOTH missing and unexpected skill files, so this
        is what proves the manifest and the tree match exactly."""
        release_manifest.verify_source_tree()


class HistoricalArtifactTests(unittest.TestCase):
    def test_no_historical_run_artifact_is_written_by_this_suite(self) -> None:
        """An explicit ticket requirement: historical runs and artifacts are never
        modified. This asserts the property structurally -- no module under test names a
        historical run directory as a write target."""
        runs = REPO_ROOT / "artifacts" / "runs"
        if not runs.is_dir():
            self.skipTest("no run artifacts in this checkout")
        for name in ("decision_contract.py", "decision_gate.py", "task_context.py"):
            source = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn("write_text(", source, f"{name} writes files")


    # ---- the same requirement, asserted on BEHAVIOUR rather than on a grep -----------
    CURRENT_RUN = "run_70401d3e9964"

    def historical_digest(self) -> dict:
        """sha256 of every artifact belonging to a run that is not the current one."""
        runs = REPO_ROOT / "artifacts" / "runs"
        digest = {}
        for path in sorted(runs.rglob("*")):
            if not path.is_file() or self.CURRENT_RUN in path.parts:
                continue
            digest[str(path.relative_to(runs))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
        return digest

    def test_the_feature_write_paths_touch_no_historical_run_artifact(self) -> None:
        """The structural test above greps three modules for `write_text(`.

        That is a proxy, and a weak one: it cannot see a write through `open(..., "w")`,
        a write from any module it does not name, or a sink that ignores the artifact
        base it was handed and falls back to the repository root. This exercises the
        actual OS-42 write path -- the audit sink, for all four transitions -- against a
        temporary base, and requires every historical run artifact to be byte-identical
        afterwards.

        It fails if a write path ever resolves its root from the repository instead of
        from the base it is given, which is the concrete way this ticket's "historical
        runs and artifacts are NEVER modified" requirement would be broken.
        """
        runs = REPO_ROOT / "artifacts" / "runs"
        if not runs.is_dir():
            self.skipTest("no run artifacts in this checkout")
        from scripts.deterministic_workflow import audit
        from scripts.deterministic_workflow.audit import RunLoggingAuditSink

        before = self.historical_digest()
        self.assertTrue(before, "no historical artifacts to protect")
        with TemporaryDirectory() as directory:
            base = Path(directory)
            # A run id that names a REAL historical run: if the base were ignored, the
            # write would land on that run's directory and the digest would move.
            historical = sorted(
                path.name for path in runs.iterdir()
                if path.is_dir() and path.name != self.CURRENT_RUN)
            sink = RunLoggingAuditSink(historical[0], artifact_base=base)
            for index, event in enumerate(audit.AUDIT_EVENTS):
                sink.deliver(event, f"key-{index}",
                             {"phase": "TEST", "role": "worker", "detail": "x"})
            written = list((base / "artifacts" / "runs").rglob("*.md"))
            self.assertTrue(written, "the sink wrote nothing under the base it was given")
        self.assertEqual(self.historical_digest(), before,
                         "a historical run artifact changed while exercising OS-42's "
                         "own write path")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
