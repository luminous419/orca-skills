"""OS-42: artifact identity, the one-based gate_iteration derivation, and drift.

The gate_iteration tests are the F-002 correction: `phase_iterations[phase]` is
zero-based and post-incremented, so feeding it raw to a one-based suffix ladder gives
review 1 the path `REVIEW_<PHASE>_iteration0.md` and maps review 2 onto review 1's
unsuffixed file -- silently overwriting evidence.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
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


def _langgraph_ok() -> bool:
    """The OS-40 checkpoint store (used by the history-lock fixtures) needs the pinned LangGraph."""
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try:
        import importlib.metadata
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:
        return False


NEEDS_CHECKPOINT_STORE = unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")


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
    #: OS-44 coordinator-session binding written into an orchestrated run's directory.
    ACTIVE_BINDING_SCHEMA = "os44.coordinator_session_binding.v1"
    #: The current invocation names its active run(s) here (comma-separated run ids) -- a
    #: lane run inside an orchestrated worktree MUST name the run it belongs to, e.g.
    #: `OS42_ACTIVE_RUN_IDS=run_f820764749d6 python3 -m scripts.ci_lane --lane present run`;
    #: without it every run on disk is history unless a POSITIVE live-writer fact exists, and a
    #: concurrent writer under an unnamed active run is (honestly) detected as a modification
    #: of history.
    ACTIVE_RUNS_ENV = "OS42_ACTIVE_RUN_IDS"
    #: `run_status` values that END a run (OS-42 `RUN_STATUS_VALUES` minus WAITING_FOR_INPUT).
    TERMINAL_RUN_STATUSES = frozenset({"COMPLETED", "BLOCKED", "ERROR", "ESCALATED", "CANCELLED", "ABANDONED"})

    @classmethod
    def active_runs(cls, runs: Path, env: "dict | None" = None) -> set:
        """The run ids that are ACTIVE for this invocation -- and nothing inferred:
          * the explicit invocation scope `OS42_ACTIVE_RUN_IDS`;
          * a POSITIVE live-writer fact: a run whose workflow checkpoint is present with a
            NON-terminal `run_status` AND whose coordinator-session binding is unreleased --
            both required (an unreleased binding alone is a cookie that names a run, not a
            writer; a non-terminal checkpoint alone may be an abandoned run).
        OS-48 iteration 8 (F-011): no hard-coded legacy run and no same-session cookie -- a
        session id identifies a run but does not prove a current writer, so a run resumed by
        the same session after its terminal checkpoint is history."""
        env = os.environ if env is None else env
        active = set()
        active.update(x.strip() for x in (env.get(cls.ACTIVE_RUNS_ENV) or "").split(",") if x.strip())
        if not runs.is_dir():
            return active
        from scripts.deterministic_workflow.turn_boundary import read_workflow_checkpoint
        base = runs.parent.parent
        for run in runs.iterdir():
            if not run.is_dir() or run.name in active:
                continue
            bindings = list((run / "coordinator_session").glob("*.json")) if (run / "coordinator_session").is_dir() else []
            unreleased = False
            for binding in bindings:
                try:
                    record = json.loads(binding.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if (isinstance(record, dict) and record.get("schema") == cls.ACTIVE_BINDING_SCHEMA
                        and record.get("run_id") == run.name and record.get("released_at") is None):
                    unreleased = True
            if not unreleased:
                continue
            try:
                state = read_workflow_checkpoint(run.name, artifact_base=base)
            except Exception:  # noqa: BLE001 - an unreadable checkpoint is no positive fact
                continue
            if state.get("present") and str(state.get("run_status") or "") not in cls.TERMINAL_RUN_STATUSES:
                active.add(run.name)
        return active

    @classmethod
    def historical_files(cls, runs: "Path | None" = None, env: "dict | None" = None) -> list:
        """Every file on disk under `artifacts/runs` -- tracked or untracked -- whose ROOT run
        directory is not an active run (:meth:`active_runs`).  Only the first path component
        decides (F-011): a settled run's archive that happens to contain a directory named
        like the active run is history."""
        runs = runs if runs is not None else REPO_ROOT / "artifacts" / "runs"
        active = cls.active_runs(runs, env)
        return [path for path in sorted(runs.rglob("*"))
                if path.is_file() and path.relative_to(runs).parts[0] not in active]

    @classmethod
    def historical_digest(cls, runs: "Path | None" = None, env: "dict | None" = None) -> dict:
        """sha256 of every historical run artifact (see :meth:`historical_files`)."""
        runs = runs if runs is not None else REPO_ROOT / "artifacts" / "runs"
        digest = {}
        for path in cls.historical_files(runs, env):
            digest[str(path.relative_to(runs))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
        return digest

    # ---- mutation controls for the lock's own scope (OS-48 iterations 6-8, F-011) -------
    def _checkpoint(self, base: Path, run: str, *, terminal: bool) -> None:
        """Commit a workflow checkpoint for ``run`` with the REAL producers (the OS-40 store
        needs the pinned LangGraph: without it these controls skip with the LangGraph
        reason and run in the present lane; the real-tree lock below runs in both)."""
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        from scripts.deterministic_workflow.state import initial_state
        state = dict(initial_state(run_id=run, thread_id="thread_main", phases=("ANALYSIS", "PLAN"),
                                   capabilities=BASE_CAPABILITIES))
        if terminal:
            state.update(terminal_status="COMPLETED", run_lifecycle="SETTLED", pending_role=None, route_token="COMPLETE")
        cp = {"v": 1, "id": "cp", "ts": "2026-01-01T00:00:00Z", "channel_values": state,
              "channel_versions": {k: 1 for k in state}, "versions_seen": {}, "pending_sends": []}
        FileCheckpointSaver(base / "artifacts" / "runs" / run / ".workflow_checkpoints.json").put(
            {"configurable": {"thread_id": "thread_main", "checkpoint_ns": ""}}, cp, {"source": "loop", "step": 0},
            {k: 1 for k in state})

    def _fixture_runs(self) -> Path:
        """A runs tree with: a settled run git does not know; a settled run whose coordinator
        died right after a terminal COMPLETED checkpoint, leaving its binding UNRELEASED (the
        reviewer's stale-cookie cut, any session); a settled run whose archive contains a
        directory named like the active run; a settled root named like OS-42's legacy run id;
        a run that is LIVE by the positive fact (non-terminal checkpoint + unreleased binding);
        and the ACTIVE run named by the invocation.  Bindings/checkpoints come from the real
        producers."""
        from scripts.deterministic_workflow import turn_boundary as tb
        base = Path(self.enterContext(TemporaryDirectory()))
        runs = base / "artifacts" / "runs"
        (runs / "run_settleduntracked").mkdir(parents=True)
        (runs / "run_settleduntracked" / "accepted.md").write_text("accepted historical evidence")
        self.assertTrue(tb.bind_session_run("run_settledcrashed", session_id="coordinator-that-died", artifact_base=base))
        self._checkpoint(base, "run_settledcrashed", terminal=True)
        (runs / "run_settledcrashed" / "FINAL_REVIEW.md").write_text("terminal COMPLETED checkpoint committed; binding never released")
        (runs / "run_settledarchive" / "evidence" / "run_active0").mkdir(parents=True)
        (runs / "run_settledarchive" / "evidence" / "run_active0" / "accepted.md").write_text("accepted historical archive")
        (runs / "run_70401d3e9964").mkdir()
        (runs / "run_70401d3e9964" / "accepted.md").write_text("OS-42's own run is history like any other")
        self.assertTrue(tb.bind_session_run("run_live0", session_id="a-live-coordinator", artifact_base=base))
        self._checkpoint(base, "run_live0", terminal=False)
        (runs / "run_live0" / "evidence").mkdir()
        (runs / "run_live0" / "evidence" / "log.txt").write_text("line 1\n")
        self.assertTrue(tb.bind_session_run("run_active0", session_id="the-live-coordinator", artifact_base=base))
        (runs / "run_active0" / "evidence").mkdir()
        (runs / "run_active0" / "evidence" / "log.txt").write_text("line 1\n")
        return runs

    ACTIVE_ENV = {"OS42_ACTIVE_RUN_IDS": "run_active0", "CLAUDE_CODE_SESSION_ID": ""}
    EMPTY_ENV = {"OS42_ACTIVE_RUN_IDS": "", "CLAUDE_CODE_SESSION_ID": ""}

    @NEEDS_CHECKPOINT_STORE
    def test_an_untracked_settled_change_or_deletion_is_detected(self) -> None:
        runs = self._fixture_runs()
        self.assertEqual(self.active_runs(runs, self.ACTIVE_ENV), {"run_active0", "run_live0"})
        before = self.historical_digest(runs, self.ACTIVE_ENV)
        self.assertIn("run_settleduntracked/accepted.md", before)
        self.assertFalse([k for k in before if k.startswith(("run_active0/", "run_live0/"))], before)
        (runs / "run_settleduntracked" / "accepted.md").write_text("changed historical evidence")
        self.assertNotEqual(self.historical_digest(runs, self.ACTIVE_ENV), before, "an untracked settled change went undetected")
        (runs / "run_settleduntracked" / "accepted.md").write_text("accepted historical evidence")
        self.assertEqual(self.historical_digest(runs, self.ACTIVE_ENV), before)
        (runs / "run_settleduntracked" / "accepted.md").unlink()
        self.assertNotEqual(self.historical_digest(runs, self.ACTIVE_ENV), before, "a settled deletion went undetected")

    @NEEDS_CHECKPOINT_STORE
    def test_an_empty_invocation_protects_every_settled_root_including_the_legacy_id(self) -> None:
        """`probe_history_hardcoded_active`: with nothing named, only the positive live-writer
        fact is active; OS-42's legacy run id is history like any other root."""
        runs = self._fixture_runs()
        self.assertEqual(self.active_runs(runs, self.EMPTY_ENV), {"run_live0"})
        before = self.historical_digest(runs, self.EMPTY_ENV)
        self.assertIn("run_70401d3e9964/accepted.md", before)
        self.assertIn("run_active0/evidence/log.txt", before)          # unnamed and no live fact: history
        (runs / "run_70401d3e9964" / "accepted.md").write_text("changed")
        self.assertNotEqual(self.historical_digest(runs, self.EMPTY_ENV), before, "the legacy root went unprotected")

    @NEEDS_CHECKPOINT_STORE
    def test_a_terminal_run_with_an_unreleased_binding_is_protected_for_any_session(self) -> None:
        """`probe_history_terminal_stale_binding` + `probe_history_own_session_terminal`: a
        terminal checkpoint with an unreleased cookie is history -- for another session and
        for the SAME session resuming it (a cookie names a run; it is not a writer)."""
        runs = self._fixture_runs()
        for env in (self.ACTIVE_ENV, self.EMPTY_ENV, {"OS42_ACTIVE_RUN_IDS": "", "CLAUDE_CODE_SESSION_ID": "coordinator-that-died"}):
            self.assertNotIn("run_settledcrashed", self.active_runs(runs, env), env)
            before = self.historical_digest(runs, env)
            self.assertIn("run_settledcrashed/FINAL_REVIEW.md", before)
            (runs / "run_settledcrashed" / "FINAL_REVIEW.md").write_text(f"changed settled evidence {env}")
            self.assertNotEqual(self.historical_digest(runs, env), before, env)

    @NEEDS_CHECKPOINT_STORE
    def test_a_nested_active_name_inside_a_settled_run_is_protected(self) -> None:
        """`probe_history_nested_active_name`: only the ROOT run directory decides."""
        runs = self._fixture_runs()
        before = self.historical_digest(runs, self.ACTIVE_ENV)
        self.assertIn("run_settledarchive/evidence/run_active0/accepted.md", before)
        (runs / "run_settledarchive" / "evidence" / "run_active0" / "accepted.md").write_text("changed historical archive")
        self.assertNotEqual(self.historical_digest(runs, self.ACTIVE_ENV), before, "a nested active-name path went unprotected")

    @NEEDS_CHECKPOINT_STORE
    def test_an_active_run_evidence_append_is_excluded(self) -> None:
        """The named active run's writers and the positively live run's writers never move the
        digest; once the invocation stops naming a run (and it has no live fact) it is history."""
        runs = self._fixture_runs()
        before = self.historical_digest(runs, self.ACTIVE_ENV)
        for run in ("run_active0", "run_live0"):
            with (runs / run / "evidence" / "log.txt").open("a") as handle:
                handle.write("line 2 (a concurrent evidence writer)\n")
            (runs / run / "evidence" / "new_probe.txt").write_text("more evidence")
        self.assertEqual(self.historical_digest(runs, self.ACTIVE_ENV), before)
        after = self.historical_digest(runs, self.EMPTY_ENV)
        self.assertIn("run_active0/evidence/new_probe.txt", after)
        self.assertNotIn("run_live0/evidence/new_probe.txt", after)

    @NEEDS_CHECKPOINT_STORE
    def test_the_positive_live_writer_fact_needs_both_halves(self) -> None:
        """Non-terminal checkpoint + unreleased binding = active; either half alone is not."""
        from scripts.deterministic_workflow import turn_boundary as tb
        runs = self._fixture_runs()
        base = runs.parent.parent
        self.assertIn("run_live0", self.active_runs(runs, self.EMPTY_ENV))
        tb.release_session_run("run_live0", session_id="a-live-coordinator", artifact_base=base)
        self.assertNotIn("run_live0", self.active_runs(runs, self.EMPTY_ENV), "a released binding is no writer")
        self.assertTrue(tb.bind_session_run("run_live0", session_id="a-live-coordinator", artifact_base=base))
        self._checkpoint(base, "run_live0", terminal=True)
        self.assertNotIn("run_live0", self.active_runs(runs, self.EMPTY_ENV), "a terminal checkpoint is no writer")

    def test_the_real_tree_protects_every_settled_run_tracked_or_not(self) -> None:
        """On this checkout: every git-tracked run artifact is in the digest, every file of
        every non-active run directory is in the digest (untracked included), and nothing of
        an active run is."""
        import subprocess
        runs = REPO_ROOT / "artifacts" / "runs"
        if not runs.is_dir():
            self.skipTest("no run artifacts in this checkout")
        active = self.active_runs(runs)
        digest = self.historical_digest(runs)
        try:
            tracked = subprocess.run(["git", "ls-files", "-z", "--", str(runs)], cwd=REPO_ROOT,
                                     capture_output=True, check=True).stdout.split(b"\0")
        except (OSError, subprocess.CalledProcessError):
            tracked = []
        for entry in tracked:
            if not entry:
                continue
            rel = str((REPO_ROOT / entry.decode("utf-8")).relative_to(runs))
            if Path(rel).parts[0] not in active:
                self.assertIn(rel, digest, f"tracked history dropped: {rel}")
        on_disk = {str(p.relative_to(runs)) for p in runs.rglob("*")
                   if p.is_file() and p.relative_to(runs).parts[0] not in active}
        self.assertEqual(on_disk, set(digest), "an untracked settled artifact is unprotected")
        self.assertFalse([k for k in digest if Path(k).parts[0] in active])

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
            historical = sorted({
                path.relative_to(runs).parts[0] for path in self.historical_files()})
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
